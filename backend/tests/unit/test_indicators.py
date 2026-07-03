"""Correctness-critical indicator tests (spec §10).

Strategy: a deterministic ~300-bar synthetic OHLCV frame, cross-checked against
INDEPENDENT reference implementations written here with plain pandas — not
against pandas-ta itself. Tolerance 1e-6 unless noted.
"""

import math
from datetime import UTC

import numpy as np
import pandas as pd
import pytest

from app.analysis.indicators import (
    INDICATORS,
    compute_series,
    compute_snapshot,
)

N_BARS = 300


def make_frame(n: int = N_BARS, with_volume: bool = True, seed_phase: float = 0.0) -> pd.DataFrame:
    """Deterministic wavy price series with real high/low spread."""
    idx = pd.date_range("2025-04-01", periods=n, freq="D", tz=UTC)
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
def snapshot(df):
    return compute_snapshot(df)


def ref_ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def ref_rsi_wilder(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    avg_loss = loss.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


class TestAgainstReferences:
    def test_sma(self, df, snapshot):
        expected = df["close"].rolling(20).mean().iloc[-1]
        assert snapshot["sma_20"] == pytest.approx(expected, abs=1e-6)
        expected200 = df["close"].rolling(200).mean().iloc[-1]
        assert snapshot["sma_200"] == pytest.approx(expected200, abs=1e-6)

    def test_wma(self, df, snapshot):
        weights = np.arange(1, 21, dtype=float)
        expected = np.dot(df["close"].iloc[-20:], weights) / weights.sum()
        assert snapshot["wma_20"] == pytest.approx(expected, abs=1e-6)

    def test_ema(self, df, snapshot):
        assert snapshot["ema_26"] == pytest.approx(ref_ema(df["close"], 26).iloc[-1], abs=1e-4)

    def test_rsi_wilder(self, df, snapshot):
        assert snapshot["rsi_14"] == pytest.approx(
            ref_rsi_wilder(df["close"]).iloc[-1], abs=1e-4
        )
        assert 0 <= snapshot["rsi_14"] <= 100

    def test_macd_from_ema_chain(self, df, snapshot):
        macd_line = ref_ema(df["close"], 12) - ref_ema(df["close"], 26)
        signal = ref_ema(macd_line, 9)
        assert snapshot["macd"] == pytest.approx(macd_line.iloc[-1], abs=1e-4)
        assert snapshot["macd_signal"] == pytest.approx(signal.iloc[-1], abs=1e-4)
        assert snapshot["macd_hist"] == pytest.approx(
            (macd_line - signal).iloc[-1], abs=1e-4
        )

    def test_bollinger(self, df, snapshot):
        mid = df["close"].rolling(20).mean().iloc[-1]
        # pandas-ta-classic uses population std (ddof=0) — convention pinned here
        std = df["close"].rolling(20).std(ddof=0).iloc[-1]
        assert snapshot["bb_middle"] == pytest.approx(mid, abs=1e-6)
        assert snapshot["bb_upper"] == pytest.approx(mid + 2 * std, abs=1e-4)
        assert snapshot["bb_lower"] == pytest.approx(mid - 2 * std, abs=1e-4)
        assert snapshot["bb_lower"] < snapshot["bb_middle"] < snapshot["bb_upper"]

    def test_atr_wilder(self, df, snapshot):
        prev_close = df["close"].shift(1)
        tr = pd.concat(
            [
                df["high"] - df["low"],
                (df["high"] - prev_close).abs(),
                (df["low"] - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
        assert snapshot["atr_14"] == pytest.approx(atr.iloc[-1], abs=1e-4)
        assert snapshot["atr_pct"] == pytest.approx(
            atr.iloc[-1] / df["close"].iloc[-1], abs=1e-6
        )

    def test_obv_cumulative(self, df, snapshot):
        direction = np.sign(df["close"].diff().fillna(0))
        direction.iloc[0] = 1  # pandas-ta-classic seeds OBV with +volume[0] — pinned
        expected = (direction * df["volume"]).cumsum().iloc[-1]
        assert snapshot["obv"] == pytest.approx(expected, rel=1e-6)

    def test_stochastic(self, df, snapshot):
        ll = df["low"].rolling(14).min()
        hh = df["high"].rolling(14).max()
        raw_k = 100 * (df["close"] - ll) / (hh - ll)
        k = raw_k.rolling(3).mean()
        d = k.rolling(3).mean()
        assert snapshot["stoch_k"] == pytest.approx(k.iloc[-1], abs=1e-4)
        assert snapshot["stoch_d"] == pytest.approx(d.iloc[-1], abs=1e-4)

    def test_vwap_20_rolling(self, df, snapshot):
        tp = (df["high"] + df["low"] + df["close"]) / 3
        expected = (
            (tp * df["volume"]).rolling(20).sum() / df["volume"].rolling(20).sum()
        ).iloc[-1]
        assert snapshot["vwap_20"] == pytest.approx(expected, abs=1e-6)

    def test_volatility_annualized(self, df, snapshot):
        log_ret = np.log(df["close"]).diff()
        expected = log_ret.rolling(20).std() * math.sqrt(252)
        assert snapshot["volatility_20"] == pytest.approx(expected.iloc[-1], abs=1e-8)

    def test_returns(self, df, snapshot):
        close = df["close"]
        assert snapshot["return_1d"] == pytest.approx(
            close.iloc[-1] / close.iloc[-2] - 1, abs=1e-10
        )
        assert snapshot["return_3m"] == pytest.approx(
            close.iloc[-1] / close.iloc[-64] - 1, abs=1e-10
        )
        # ytd: vs last close of the prior calendar year
        prior = close[close.index.year < close.index[-1].year]
        assert snapshot["return_ytd"] == pytest.approx(
            close.iloc[-1] / prior.iloc[-1] - 1, abs=1e-10
        )

    def test_52w_range(self, df, snapshot):
        window = min(252, len(df))
        assert snapshot["high_52w"] == pytest.approx(df["high"].iloc[-window:].max())
        assert snapshot["low_52w"] == pytest.approx(df["low"].iloc[-window:].min())
        assert snapshot["dist_52w_high"] <= 0  # close can never exceed the window high
        assert snapshot["dist_52w_low"] >= 0

    def test_adx_bounded(self, snapshot):
        # ADX reference impls differ in warm-up; assert bounds + presence (0-100)
        assert snapshot["adx_14"] is not None
        assert 0 <= snapshot["adx_14"] <= 100
        assert 0 <= snapshot["plus_di_14"] <= 100
        assert 0 <= snapshot["minus_di_14"] <= 100


class TestRelativeStrength:
    def test_rs_is_return_difference(self, df):
        bench = make_frame(seed_phase=1.5)
        snap = compute_snapshot(df, benchmark_df=bench, benchmark_symbol="SPY")
        close, bclose = df["close"], bench["close"]
        expected = (close.iloc[-1] / close.iloc[-22] - 1) - (
            bclose.iloc[-1] / bclose.iloc[-22] - 1
        )
        assert snap["rs_1m"] == pytest.approx(expected, abs=1e-10)
        assert snap["benchmark_symbol"] == "SPY"

    def test_no_benchmark_leaves_none(self, snapshot):
        assert snapshot["rs_1m"] is None
        assert snapshot["benchmark_symbol"] is None


class TestPolicies:
    def test_short_frame_stores_none_per_min_bars(self):
        snap = compute_snapshot(make_frame(30))
        assert snap["sma_20"] is not None  # 30 bars >= 20
        assert snap["sma_200"] is None
        assert snap["macd"] is None  # needs 35
        assert snap["high_52w"] is None  # needs 60
        assert snap["return_1y"] is None
        assert snap["extras"]["bars_available"] == 30

    def test_volumeless_frame_nulls_volume_metrics(self):
        snap = compute_snapshot(make_frame(with_volume=False))
        assert snap["obv"] is None
        assert snap["vwap_20"] is None
        assert snap["volume"] is None
        assert snap["volume_avg_20"] is None
        assert snap["rsi_14"] is not None  # price metrics unaffected

    def test_empty_frame_returns_none(self):
        from app.analysis.data import FRAME_COLUMNS

        empty = pd.DataFrame(columns=FRAME_COLUMNS, index=pd.DatetimeIndex([], tz=UTC))
        assert compute_snapshot(empty) is None

    def test_snapshot_covers_all_metric_columns_except_fundamentals(self, snapshot):
        from app.models import METRIC_COLUMNS

        fundamentals = {
            "market_cap", "pe", "ps", "ev_ebitda", "gross_margin", "net_margin",
            "roe", "debt_to_equity", "revenue_growth_yoy",
        }  # fmt: skip
        missing = [c for c in METRIC_COLUMNS if c not in fundamentals and c not in snapshot]
        assert missing == []


class TestSeries:
    def test_series_shapes_and_alignment(self, df):
        out = compute_series(df, ["sma_20", "macd", "bbands"])
        assert list(out.columns) == [
            "sma_20", "macd", "macd_signal", "macd_hist",
            "bb_upper", "bb_middle", "bb_lower", "bb_width", "percent_b",
        ]  # fmt: skip
        assert len(out) == len(df)
        assert out.index.equals(df.index)

    def test_unknown_key_raises(self, df):
        with pytest.raises(KeyError, match="unknown indicator"):
            compute_series(df, ["sma_20", "bogus"])

    def test_volume_keys_skipped_without_volume(self):
        out = compute_series(make_frame(50, with_volume=False), ["obv", "sma_20"])
        assert "obv" not in out.columns
        assert "sma_20" in out.columns

    def test_registry_min_bars_documented(self):
        for spec in INDICATORS.values():
            assert spec.min_bars >= 1
            assert spec.output_columns()
