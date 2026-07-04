"""Point-in-time consistency: panel value at t == compute_snapshot on data up to t.

This is the look-ahead-bias lock for screen backtests — if a panel definition
drifts from the live snapshot definition, backtests stop testing the screen the
user actually runs.
"""

import math
from datetime import UTC

import numpy as np
import pandas as pd
import pytest

from app.analysis.indicators import compute_snapshot
from app.backtest.panel import COLUMN_TO_SPEC, _asset_field_series
from app.screener.ast import BACKTESTABLE_FIELDS

N_BARS = 400


def make_frame(n: int = N_BARS, with_volume: bool = True, seed_phase: float = 0.0) -> pd.DataFrame:
    idx = pd.date_range("2024-06-01", periods=n, freq="D", tz=UTC)
    t = np.arange(n, dtype=float)
    close = 100 + 0.05 * t + 10 * np.sin(t / 12 + seed_phase) + 3 * np.sin(t / 5 + seed_phase)
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    high = np.maximum(open_, close) + 1.5 + np.abs(np.sin(t / 7))
    low = np.minimum(open_, close) - 1.5 - np.abs(np.cos(t / 9))
    volume = 1e6 + 1e5 * np.sin(t / 3) ** 2 if with_volume else np.full(n, np.nan)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx
    )


@pytest.fixture(scope="module")
def df():
    return make_frame()


@pytest.fixture(scope="module")
def benchmark_close():
    return make_frame(seed_phase=1.3)["close"].astype(float)


@pytest.fixture(scope="module")
def panel(df, benchmark_close):
    return _asset_field_series(df, BACKTESTABLE_FIELDS, benchmark_close)


class TestCompleteness:
    def test_every_backtestable_field_resolves(self, panel, df):
        assert set(panel) == BACKTESTABLE_FIELDS
        for field, series in panel.items():
            assert len(series) == len(df), field

    def test_non_pit_field_raises(self, df):
        with pytest.raises(KeyError, match="not point-in-time"):
            _asset_field_series(df, {"pe"}, None)


class TestPitConsistency:
    # every panel definition family, sampled at several truncation points
    FIELDS = (
        "close",
        "volume_avg_20",
        "return_1m",
        "return_ytd",
        "sma_20",
        "rsi_14",
        "macd_hist",
        "bb_width",
        "atr_pct",
        "adx_14",
        "stoch_d",
        "obv",
        "vwap_20",
        "volatility_20",
        "high_52w",
        "dist_52w_high",
        "low_52w",
        "rs_1m",
        "rs_3m",
    )

    @pytest.mark.parametrize("t_index", [320, 360, N_BARS - 1])
    @pytest.mark.parametrize("field", FIELDS)
    def test_panel_equals_snapshot_on_truncated_frame(
        self, df, benchmark_close, panel, field, t_index
    ):
        truncated = df.iloc[: t_index + 1]
        bench_df = pd.DataFrame({"close": benchmark_close.iloc[: t_index + 1]})
        snapshot = compute_snapshot(truncated, benchmark_df=bench_df, benchmark_symbol="SPY")

        panel_value = panel[field].iloc[t_index]
        snapshot_value = snapshot[field]

        if snapshot_value is None:
            assert math.isnan(panel_value), f"{field}@{t_index}: panel={panel_value}"
        else:
            assert panel_value == pytest.approx(snapshot_value, abs=1e-6), f"{field}@{t_index}"

    def test_warmup_bars_are_nan_not_filled(self, df):
        series = _asset_field_series(df, {"sma_200"}, None)["sma_200"]
        assert series.iloc[:199].isna().all()
        assert not math.isnan(series.iloc[250])

    def test_no_volume_gates_volume_fields(self):
        forex = make_frame(with_volume=False)
        fields = _asset_field_series(forex, {"obv", "vwap_20", "volume_avg_20"}, None)
        for field, series in fields.items():
            assert series.isna().all(), field


class TestColumnToSpec:
    def test_covers_all_indicator_outputs(self):
        assert COLUMN_TO_SPEC["macd_hist"] == "macd"
        assert COLUMN_TO_SPEC["bb_upper"] == "bbands"
        assert COLUMN_TO_SPEC["rsi_14"] == "rsi_14"
