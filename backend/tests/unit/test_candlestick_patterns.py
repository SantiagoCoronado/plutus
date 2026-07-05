"""Candlestick vocabulary (spec §13.5): hand-built bars per pattern, plus the
regression that keeps pattern columns OUT of the asset_metrics snapshot (the
nightly upsert splats every snapshot key into table columns)."""

import numpy as np
import pandas as pd
import pytest

from app.analysis.indicators import INDICATORS, compute_series, compute_snapshot
from app.backtest.strategy import STRATEGY_FIELDS
from app.models.asset_metrics import METRIC_COLUMNS

PATTERNS = ("bullish_engulfing", "bearish_engulfing", "hammer", "shooting_star", "doji")


def frame(bars: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """bars = [(open, high, low, close), ...]"""
    index = pd.date_range("2026-01-05", periods=len(bars), freq="D", tz="UTC")
    df = pd.DataFrame(bars, columns=["open", "high", "low", "close"], index=index)
    df["volume"] = 1e6
    return df


def last_value(df: pd.DataFrame, key: str) -> float:
    series = compute_series(df, [key])[key]
    return float(series.iloc[-1])


class TestPatternMath:
    def test_bullish_engulfing_positive(self):
        # red candle fully swallowed by a bigger green one
        df = frame([(100, 101, 97, 98), (97.5, 101.5, 97, 101)])
        assert last_value(df, "bullish_engulfing") == 1.0

    def test_bullish_engulfing_negative_when_not_engulfed(self):
        df = frame([(100, 101, 97, 98), (98.5, 100.5, 98, 99.5)])  # inside bar
        assert last_value(df, "bullish_engulfing") == 0.0

    def test_bearish_engulfing_positive(self):
        df = frame([(98, 101, 97.5, 100), (100.5, 101.5, 96, 97)])
        assert last_value(df, "bearish_engulfing") == 1.0

    def test_bearish_engulfing_negative_on_green_candle(self):
        df = frame([(98, 101, 97.5, 100), (99, 103, 98.5, 102)])
        assert last_value(df, "bearish_engulfing") == 0.0

    def test_hammer_positive(self):
        # long lower wick (6), small body (1) near the top, tiny upper wick (0.5)
        df = frame([(100, 100.5, 93, 99)])
        assert last_value(df, "hammer") == 1.0

    def test_hammer_negative_when_upper_wick_large(self):
        df = frame([(100, 104, 93, 99)])  # upper wick 5 > body 1
        assert last_value(df, "hammer") == 0.0

    def test_shooting_star_positive(self):
        df = frame([(99, 106, 98.5, 100)])  # upper wick 6, body 1, lower wick 0.5
        assert last_value(df, "shooting_star") == 1.0

    def test_shooting_star_negative_for_hammer_shape(self):
        df = frame([(100, 100.5, 93, 99)])
        assert last_value(df, "shooting_star") == 0.0

    def test_doji_positive(self):
        df = frame([(100, 102, 98, 100.1)])  # body 0.1 vs range 4
        assert last_value(df, "doji") == 1.0

    def test_doji_negative_for_full_body(self):
        df = frame([(98, 102, 97.8, 102)])
        assert last_value(df, "doji") == 0.0

    def test_single_bar_frame_skips_engulfing(self):
        # min_bars=2: a 1-bar frame can't evaluate shift(1), so the spec is gated
        df = frame([(97.5, 101.5, 97, 101)])
        series = compute_series(df, ["bullish_engulfing"])
        assert "bullish_engulfing" not in series.columns

    def test_first_bar_of_longer_frame_never_fires(self):
        df = frame([(97.5, 101.5, 97, 101), (100, 102, 99, 101)])
        series = compute_series(df, ["bullish_engulfing"])
        assert float(series["bullish_engulfing"].iloc[0]) == 0.0


class TestVocabularyWiring:
    def test_patterns_are_strategy_fields(self):
        assert set(PATTERNS) <= STRATEGY_FIELDS

    def test_patterns_never_in_screen_metrics(self):
        assert not set(PATTERNS) & set(METRIC_COLUMNS)

    def test_snapshot_keyset_stays_within_metric_columns(self):
        """THE regression: every snapshot key must be an asset_metrics column,
        or the nightly upsert crashes on an unknown column."""
        closes = np.linspace(100, 120, 300)
        df = frame([(c - 0.5, c + 1.0, c - 1.5, c) for c in closes])
        snapshot = compute_snapshot(df)
        allowed = set(METRIC_COLUMNS) | {"as_of", "extras", "benchmark_symbol"}
        assert set(snapshot) <= allowed
        assert not set(PATTERNS) & set(snapshot)

    @pytest.mark.parametrize("pattern", PATTERNS)
    def test_pattern_specs_marked_no_snapshot(self, pattern):
        assert INDICATORS[pattern].snapshot is False
