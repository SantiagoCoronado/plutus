"""publish_quote fans a tick to the pub/sub channel AND stashes a short-TTL
last-quote key; read_last_quotes replays only the symbols that have one.

Async is driven via asyncio.run (no pytest-asyncio in the dev deps) over
fakeredis.aioredis, which ships pub/sub + setex support.
"""

import asyncio
import json

import fakeredis.aioredis

from app.quotes.publisher import CHANNEL, LAST_TTL_S, publish_quote, read_last_quotes

TICK = {"symbol": "BTC", "price": 50000.0, "change_pct": 2.04, "ts": "2026-07-06T00:00:00+00:00",
        "source": "binance"}


def _run(coro):
    return asyncio.run(coro)


def test_publish_writes_channel_and_last_key():
    async def scenario():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        pubsub = redis.pubsub()
        await pubsub.subscribe(CHANNEL)

        await publish_quote(redis, TICK)

        message = None
        for _ in range(20):
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
            if message is not None:
                break
        assert message is not None, "tick was not published to the channel"
        assert json.loads(message["data"])["symbol"] == "BTC"

        raw = await redis.get("quote:last:BTC")
        assert raw is not None and json.loads(raw)["price"] == 50000.0
        ttl = await redis.ttl("quote:last:BTC")
        assert 0 < ttl <= LAST_TTL_S

        await pubsub.aclose()
        await redis.aclose()

    _run(scenario())


def test_last_key_uses_uppercase_symbol():
    async def scenario():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await publish_quote(redis, {**TICK, "symbol": "eth"})
        assert await redis.get("quote:last:ETH") is not None
        await redis.aclose()

    _run(scenario())


def test_read_last_quotes_skips_missing():
    async def scenario():
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await publish_quote(redis, TICK)

        result = await read_last_quotes(redis, ["btc", "ETH"])
        assert set(result) == {"BTC"}
        assert result["BTC"]["price"] == 50000.0
        assert await read_last_quotes(redis, []) == {}

        await redis.aclose()

    _run(scenario())
