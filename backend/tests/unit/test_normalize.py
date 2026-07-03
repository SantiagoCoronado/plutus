import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.ingestion.normalize import candles_to_rows
from app.providers.base import CANDLE_COLUMNS, ProviderRateLimitError
from app.providers.coingecko import CoinGeckoProvider
from app.providers.tiingo import TiingoProvider
from app.providers.twelvedata import TwelveDataProvider
from app.schemas.common import Interval

FIXTURES = Path(__file__).parent.parent / "fixtures"


def load(name: str):
    return json.loads((FIXTURES / name).read_text())


def assert_canonical(df):
    assert list(df.columns) == CANDLE_COLUMNS
    assert str(df["ts"].dt.tz) == "UTC"
    assert (df["ts"] == df["ts"].dt.normalize()).all()  # UTC midnight convention
    assert df["ts"].is_monotonic_increasing


class TestTiingo:
    def test_parses_adjusted_series(self):
        df = TiingoProvider._parse_ohlcv(load("tiingo_daily.json"))
        assert_canonical(df)
        assert len(df) == 3
        # the 2026-06-30 row has raw close 430.0 but adjClose 215.0 (2:1 split):
        # the ADJUSTED series must be what we store
        row = df[df["ts"] == datetime(2026, 6, 30, tzinfo=UTC)].iloc[0]
        assert row["close"] == 215.0
        assert row["open"] == 213.0
        assert row["volume"] == 48211000

    def test_empty_payload(self):
        assert TiingoProvider._parse_ohlcv([]).empty


class TestCoinGecko:
    def test_synthesizes_candles(self):
        df = CoinGeckoProvider._parse_ohlcv(load("coingecko_market_chart.json"))
        assert_canonical(df)
        assert len(df) == 4
        # synthetic construction: open = previous close, H/L = max/min(open, close)
        assert df.iloc[0]["open"] == df.iloc[0]["close"]  # first bar has no prior close
        for i in range(1, len(df)):
            assert df.iloc[i]["open"] == df.iloc[i - 1]["close"]
            assert df.iloc[i]["high"] == max(df.iloc[i]["open"], df.iloc[i]["close"])
            assert df.iloc[i]["low"] == min(df.iloc[i]["open"], df.iloc[i]["close"])
        assert df.iloc[1]["volume"] == 30987654321.0

    def test_empty_payload(self):
        assert CoinGeckoProvider._parse_ohlcv({"prices": []}).empty


class TestTwelveData:
    def test_parses_real_ohlc_sorted_ascending(self):
        df = TwelveDataProvider._parse_ohlcv(load("twelvedata_time_series.json"))
        assert_canonical(df)
        assert len(df) == 3
        # fixture is reverse-chronological; parser must sort ascending
        assert df.iloc[0]["ts"] == datetime(2026, 6, 29, tzinfo=UTC)
        assert df.iloc[-1]["close"] == 1.0864
        assert df["volume"].isna().all()  # forex has no volume

    def test_error_payload_maps_to_rate_limit(self):
        payload = {"code": 429, "message": "You have run out of API credits", "status": "error"}
        with pytest.raises(ProviderRateLimitError):
            TwelveDataProvider._parse_ohlcv(payload)

    def test_empty_range_400_is_empty_not_error(self):
        # exact shape Twelve Data returns when the window has no finished bar yet
        payload = {
            "code": 400,
            "message": "No data is available on the specified dates. "
            "Try setting different start/end dates.",
            "status": "error",
            "meta": {"symbol": "EUR/USD", "interval": "1day", "exchange": ""},
        }
        assert TwelveDataProvider._parse_ohlcv(payload).empty


class TestCandlesToRows:
    def test_rows_from_canonical_frame(self):
        df = TwelveDataProvider._parse_ohlcv(load("twelvedata_time_series.json"))
        rows = candles_to_rows(df, asset_id=42, interval=Interval.d1)
        assert len(rows) == 3
        first = rows[0]
        assert first["asset_id"] == 42
        assert first["interval"] == "1d"
        assert first["ts"] == datetime(2026, 6, 29, tzinfo=UTC)
        assert first["volume"] is None  # NaN -> None for DB nullability
        assert isinstance(first["close"], float)

    def test_volume_preserved_when_present(self):
        df = TiingoProvider._parse_ohlcv(load("tiingo_daily.json"))
        rows = candles_to_rows(df, asset_id=1, interval=Interval.d1)
        assert rows[0]["volume"] == 48211000.0

    def test_empty_frame_gives_no_rows(self):
        from app.providers.base import empty_candles

        assert candles_to_rows(empty_candles(), asset_id=1, interval=Interval.d1) == []
