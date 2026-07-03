import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import respx

from app.providers.base import RateLimit
from app.providers.binance import BASE_URL, DAY_MS, BinanceProvider
from app.providers.http import RateLimitedClient
from app.schemas.common import AssetClass, Interval

FIXTURES = Path(__file__).parent.parent / "fixtures"
KLINES = json.loads((FIXTURES / "binance_klines.json").read_text())

# fixture bars: 2026-06-28 .. 2026-07-01 open times; last closeTime = 2026-07-01T23:59:59.999Z
LAST_CLOSE_MS = 1782950399999


class TestParse:
    def test_parses_real_ohlc(self):
        df = BinanceProvider._parse_ohlcv(KLINES, now_ms=LAST_CLOSE_MS + 1)
        assert len(df) == 4
        assert str(df["ts"].dt.tz) == "UTC"
        assert df.iloc[0]["ts"] == datetime(2026, 6, 28, tzinfo=UTC)
        # real H/L, not synthetic max/min(open, close)
        row = df.iloc[1]
        assert row["high"] == 63500.0 and row["high"] > max(row["open"], row["close"])
        assert row["low"] == 61700.55 and row["low"] < min(row["open"], row["close"])
        assert row["volume"] == 21540.88

    def test_drops_in_progress_kline(self):
        # "now" falls inside the last kline's window -> partial bar must be dropped
        df = BinanceProvider._parse_ohlcv(KLINES, now_ms=LAST_CLOSE_MS - 1000)
        assert len(df) == 3
        assert df.iloc[-1]["ts"] == datetime(2026, 6, 30, tzinfo=UTC)

    def test_empty(self):
        assert BinanceProvider._parse_ohlcv([], now_ms=0).empty


class TestSymbols:
    def test_appends_usdt_to_bare_symbols(self):
        assert BinanceProvider._to_pair("BTC") == "BTCUSDT"
        assert BinanceProvider._to_pair("eth") == "ETHUSDT"

    def test_keeps_known_pairs(self):
        assert BinanceProvider._to_pair("BTCUSDT") == "BTCUSDT"
        assert BinanceProvider._to_pair("ETHBTC") == "ETHBTC"


@respx.mock(base_url=BASE_URL)
def test_pagination_loops_until_short_page(respx_mock, fake_redis, fake_clock):
    # page 1: 1000 klines; page 2: remainder — adapter must stitch them
    def kline(i):
        open_ms = 1600000000000 + i * DAY_MS
        return [open_ms, "1", "2", "0.5", "1.5", "10", open_ms + DAY_MS - 1] + [0] * 5

    page1 = [kline(i) for i in range(1000)]
    page2 = [kline(1000 + i) for i in range(5)]
    route = respx_mock.get("/api/v3/klines").mock(
        side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)]
    )
    client = RateLimitedClient(
        "binance",
        BASE_URL,
        fake_redis,
        RateLimit(capacity=1000, refill_amount=1000, refill_period_s=1),
        clock=fake_clock,
        sleep=fake_clock.sleep,
    )
    provider = BinanceProvider(client)
    df = provider.get_ohlcv(
        "BTC", AssetClass.crypto, Interval.d1, date(2020, 9, 13), date(2023, 6, 15)
    )
    assert route.call_count == 2
    assert len(df) == 1005
    assert df["ts"].is_monotonic_increasing
