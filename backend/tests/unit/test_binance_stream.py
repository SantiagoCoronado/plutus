"""Binance combined-stream parsing + the consumer's parse/handle path, exercised
with canned miniTicker JSON (no live socket)."""

import asyncio
import json

import fakeredis.aioredis

from app.quotes.binance_ws import (
    BinanceStreamConsumer,
    build_stream_url,
    parse_message,
    resolve_pair,
)
from app.quotes.subscriptions import SubscriptionState


def _combined(pair: str, close: str, open_: str, event_ms: int = 1_782_950_400_000) -> str:
    return json.dumps(
        {
            "stream": f"{pair.lower()}@miniTicker",
            "data": {
                "e": "24hrMiniTicker",
                "E": event_ms,
                "s": pair.upper(),
                "c": close,
                "o": open_,
                "h": "0",
                "l": "0",
                "v": "0",
                "q": "0",
            },
        }
    )


PAIR_TO_SYMBOL = {"BTCUSDT": "BTC", "ETHUSDT": "ETH"}


class TestResolvePair:
    def test_explicit_metadata_wins(self):
        meta = {"provider_symbols": {"binance": "ethusdt"}}
        assert resolve_pair("ETH", meta) == "ETHUSDT"

    def test_fallback_to_canonical_mapping(self):
        assert resolve_pair("BTC", None) == "BTCUSDT"
        assert resolve_pair("ETH", {}) == "ETHUSDT"

    def test_keeps_full_pair(self):
        assert resolve_pair("BTC", {"provider_symbols": {"binance": "BTCEUR"}}) == "BTCEUR"


class TestBuildStreamUrl:
    def test_sorted_lowercased_miniticker_streams(self):
        url = build_stream_url({"ETHUSDT", "BTCUSDT"})
        assert url == (
            "wss://stream.binance.com:9443/stream"
            "?streams=btcusdt@miniTicker/ethusdt@miniTicker"
        )


class TestParseMessage:
    def test_parses_combined_miniticker(self):
        tick = parse_message(_combined("BTCUSDT", "50000", "49000"), PAIR_TO_SYMBOL)
        assert tick is not None
        assert tick["symbol"] == "BTC"
        assert tick["price"] == 50000.0
        assert tick["change_pct"] == round((50000 - 49000) / 49000 * 100, 4)
        assert tick["source"] == "binance"
        assert tick["ts"].startswith("2026-")

    def test_parses_raw_single_stream_frame(self):
        raw = json.dumps({"e": "24hrMiniTicker", "s": "ETHUSDT", "c": "3000", "o": "3000"})
        tick = parse_message(raw, PAIR_TO_SYMBOL)
        assert tick is not None and tick["symbol"] == "ETH" and tick["change_pct"] == 0.0

    def test_unknown_pair_is_none(self):
        assert parse_message(_combined("SOLUSDT", "1", "1"), PAIR_TO_SYMBOL) is None

    def test_non_miniticker_is_none(self):
        raw = json.dumps({"data": {"e": "depthUpdate", "s": "BTCUSDT"}})
        assert parse_message(raw, PAIR_TO_SYMBOL) is None

    def test_malformed_is_none(self):
        assert parse_message("not json", PAIR_TO_SYMBOL) is None
        assert parse_message(json.dumps({"data": {"e": "24hrMiniTicker", "s": "BTCUSDT"}}),
                             PAIR_TO_SYMBOL) is None


def test_handle_raw_publishes_tick():
    async def scenario():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        state = SubscriptionState()
        state.update({"BTC": "crypto"}, {"BTCUSDT": "BTC"})
        consumer = BinanceStreamConsumer(redis, state)

        tick = await consumer.handle_raw(_combined("BTCUSDT", "51000", "50000"))
        assert tick["symbol"] == "BTC" and tick["price"] == 51000.0

        stored = json.loads(await redis.get("quote:last:crypto:BTC"))
        assert stored["price"] == 51000.0

        # an unsubscribed pair is ignored (no publish, returns None)
        assert await consumer.handle_raw(_combined("SOLUSDT", "1", "1")) is None
        assert await redis.get("quote:last:crypto:SOL") is None

        await redis.aclose()

    asyncio.run(scenario())
