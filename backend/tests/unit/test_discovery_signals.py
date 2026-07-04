"""Signal library: engineered frames with known triggers, score bands, and masks."""

import numpy as np
import pandas as pd
import pytest

from app.discovery.signals import SIGNALS, applicable_signals, composite_score


def frame(closes, volumes=None) -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    index = pd.bdate_range(end="2026-07-03", periods=len(closes), tz="UTC")
    if volumes is None:
        volumes = np.full(len(closes), np.nan)
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes * 1.01,
            "low": closes * 0.99,
            "close": closes,
            "volume": np.asarray(volumes, dtype=float),
        },
        index=index,
    )


def steady_volume(n: int) -> np.ndarray:
    # deterministic mild variation so rolling std is never zero
    return np.where(np.arange(n) % 2 == 0, 1_000_000.0, 1_100_000.0)


# ---------------------------------------------------------------- breakout


def test_breakout_triggers_on_new_high_with_volume():
    closes = np.full(130, 100.0)
    closes[-1] = 110.0
    volumes = steady_volume(130)
    volumes[-1] = 5_000_000.0
    result = SIGNALS["breakout"].compute(frame(closes, volumes), {})
    assert result is not None
    assert result.triggered
    assert result.score >= 70
    assert bool(result.mask.iloc[-1])
    assert result.evidence["volume_z"] > 1


def test_breakout_below_high_scores_by_distance_and_does_not_trigger():
    closes = np.full(130, 100.0)
    closes[100] = 110.0  # the high sits inside the trailing 55-bar window
    result = SIGNALS["breakout"].compute(frame(closes, steady_volume(130)), {})
    assert result is not None
    assert not result.triggered
    # dist = 100/110 - 1 = -9.09% -> 70 * (1 - 0.909) = 6.4
    assert result.score == pytest.approx(70 * (1 + (100 / 110 - 1) / 0.10), abs=0.1)


def test_breakout_without_volume_degrades_to_price_only():
    closes = np.full(130, 100.0)
    closes[-1] = 110.0
    result = SIGNALS["breakout"].compute(frame(closes), {})  # forex: no volume
    assert result is not None
    assert result.triggered  # mask needs no volume confirmation when none exists
    assert result.score == 70.0
    assert result.evidence["volume_z"] is None


def test_breakout_mask_marks_historical_breakouts():
    closes = np.full(200, 100.0)
    closes[100] = 120.0  # historical breakout bar
    result = SIGNALS["breakout"].compute(frame(closes), {})
    assert bool(result.mask.iloc[100])
    assert not bool(result.mask.iloc[99])


# ---------------------------------------------------------------- ma_cross


def test_ma_cross_triggers_on_fresh_golden_cross():
    closes = np.full(260, 100.0)
    closes[-5:] = 130.0  # jump lifts sma50 above sma200 at the first jump bar
    result = SIGNALS["ma_cross"].compute(frame(closes), {})
    assert result is not None
    assert result.triggered
    assert result.evidence["bars_since_cross"] == 4
    assert result.score == pytest.approx(100 * (1 - 4 / 40), abs=0.1)


def test_ma_cross_old_cross_does_not_trigger_but_still_scores():
    closes = np.full(260, 100.0)
    closes[-30:] = 130.0
    result = SIGNALS["ma_cross"].compute(frame(closes), {})
    assert result is not None
    assert not result.triggered
    assert result.evidence["bars_since_cross"] == 29
    assert 0 < result.score < 50


def test_ma_cross_flat_series_never_crosses():
    result = SIGNALS["ma_cross"].compute(frame(np.full(260, 100.0)), {})
    assert result is not None
    assert not result.triggered
    assert result.score == 0.0


# ---------------------------------------------------------------- rsi_extreme


def test_rsi_extreme_triggers_on_steady_decline():
    closes = 100 - 0.5 * np.arange(60)
    result = SIGNALS["rsi_extreme"].compute(frame(closes), {})
    assert result is not None
    assert result.triggered
    assert result.score > 90
    assert result.evidence["rsi_14"] < 10


def test_rsi_extreme_rising_series_scores_zero():
    closes = 100 + 0.5 * np.arange(60)
    result = SIGNALS["rsi_extreme"].compute(frame(closes), {})
    assert result is not None
    assert not result.triggered
    assert result.score == 0.0


# ---------------------------------------------------------------- momentum_rank


def test_momentum_rank_reads_percentile_from_ctx():
    result = SIGNALS["momentum_rank"].compute(frame(np.full(10, 100.0)), {
        "momentum_percentile": 0.9,
        "momentum_value": 0.42,
        "momentum_peers": 50,
    })
    assert result.score == 90.0
    assert result.triggered
    assert result.mask is None


def test_momentum_rank_below_80th_percentile_does_not_trigger():
    result = SIGNALS["momentum_rank"].compute(
        frame(np.full(10, 100.0)), {"momentum_percentile": 0.5}
    )
    assert result.score == 50.0
    assert not result.triggered


def test_momentum_rank_unavailable_without_ctx():
    assert SIGNALS["momentum_rank"].compute(frame(np.full(10, 100.0)), {}) is None


# ---------------------------------------------------------------- mean_reversion


def test_mean_reversion_triggers_on_sharp_drop():
    closes = np.where(np.arange(100) % 2 == 0, 100.0, 100.5)
    closes[-3:] = [92.0, 88.0, 84.0]
    result = SIGNALS["mean_reversion"].compute(frame(closes), {})
    assert result is not None
    assert result.triggered
    assert result.evidence["z_score"] < -2
    assert result.score > 50


def test_mean_reversion_flat_series_does_not_trigger():
    closes = np.where(np.arange(100) % 2 == 0, 100.0, 100.5)
    result = SIGNALS["mean_reversion"].compute(frame(closes), {})
    assert result is not None
    assert not result.triggered
    assert result.score == 0.0


# ---------------------------------------------------------------- valuation_anomaly


def test_valuation_anomaly_cheap_vs_history():
    ctx = {
        "valuation_current": {"pe": 10.0, "ps": 5.0},
        "valuation_history": {"pe": [20.0, 25.0, 30.0, 15.0], "ps": [4.0, 6.0, 8.0]},
    }
    result = SIGNALS["valuation_anomaly"].compute(frame(np.full(5, 100.0)), ctx)
    assert result is not None
    # pe: nothing in history below 10 -> 100; ps: 1 of 3 below 5 -> 66.7; mean 83.3
    assert result.score == pytest.approx(83.3, abs=0.1)
    assert result.triggered
    assert result.mask is None


def test_valuation_anomaly_expensive_scores_low():
    ctx = {
        "valuation_current": {"pe": 40.0},
        "valuation_history": {"pe": [20.0, 25.0, 30.0]},
    }
    result = SIGNALS["valuation_anomaly"].compute(frame(np.full(5, 100.0)), ctx)
    assert result.score == 0.0
    assert not result.triggered


def test_valuation_anomaly_needs_history_and_positive_values():
    base = frame(np.full(5, 100.0))
    assert SIGNALS["valuation_anomaly"].compute(base, {}) is None
    assert (
        SIGNALS["valuation_anomaly"].compute(
            base,
            {"valuation_current": {"pe": -5.0}, "valuation_history": {"pe": [10.0, 12.0, 14.0]}},
        )
        is None
    )
    assert (
        SIGNALS["valuation_anomaly"].compute(
            base, {"valuation_current": {"pe": 10.0}, "valuation_history": {"pe": [12.0, 14.0]}}
        )
        is None
    )


# ---------------------------------------------------------------- volume_anomaly


def test_volume_anomaly_triggers_on_spike():
    volumes = steady_volume(100)
    volumes[-1] = 10_000_000.0
    result = SIGNALS["volume_anomaly"].compute(frame(np.full(100, 50.0), volumes), {})
    assert result is not None
    assert result.triggered
    assert result.score > 80


def test_volume_anomaly_quiet_volume_does_not_trigger():
    result = SIGNALS["volume_anomaly"].compute(
        frame(np.full(100, 50.0), steady_volume(100)), {}
    )
    assert result is not None
    assert not result.triggered


def test_volume_anomaly_unavailable_without_volume():
    assert SIGNALS["volume_anomaly"].compute(frame(np.full(100, 50.0)), {}) is None


# ---------------------------------------------------------------- crypto_drawdown


def test_crypto_drawdown_triggers_deep_below_high_with_volume():
    closes = np.concatenate([np.linspace(50, 100, 60), np.linspace(100, 38, 240)])
    volumes = steady_volume(300)
    volumes[-1] = 6_000_000.0
    result = SIGNALS["crypto_drawdown"].compute(frame(closes, volumes), {})
    assert result is not None
    assert result.triggered
    assert result.evidence["drawdown_from_high"] < -0.5
    assert result.score > 40


def test_crypto_drawdown_shallow_dip_does_not_trigger():
    closes = np.concatenate([np.linspace(50, 100, 60), np.linspace(100, 85, 240)])
    result = SIGNALS["crypto_drawdown"].compute(frame(closes, steady_volume(300)), {})
    assert result is not None
    assert not result.triggered


# ---------------------------------------------------------------- pullback


def test_pullback_triggers_on_dip_in_uptrend():
    closes = np.linspace(100, 200, 250)
    closes[-8:] = closes[-9] * (1 - 0.015) ** np.arange(1, 9)  # 8-bar slide
    result = SIGNALS["pullback"].compute(frame(closes), {})
    assert result is not None
    assert result.triggered
    assert result.score > 50


def test_pullback_no_uptrend_scores_zero():
    closes = np.linspace(200, 100, 250)  # downtrend
    result = SIGNALS["pullback"].compute(frame(closes), {})
    assert result is not None
    assert not result.triggered
    assert result.score == 0.0


# ---------------------------------------------------------------- registry + composite


def test_applicable_signals_by_class():
    stock_keys = {spec.key for spec in applicable_signals("stock")}
    forex_keys = {spec.key for spec in applicable_signals("forex")}
    crypto_keys = {spec.key for spec in applicable_signals("crypto")}
    assert "valuation_anomaly" in stock_keys
    assert "crypto_drawdown" not in stock_keys
    assert "volume_anomaly" not in forex_keys
    assert "crypto_drawdown" in crypto_keys


def test_short_frames_return_none_for_bar_hungry_signals():
    short = frame(np.full(30, 100.0))
    assert SIGNALS["ma_cross"].compute(short, {}) is None
    assert SIGNALS["pullback"].compute(short, {}) is None


def make_result(score: float):
    from app.discovery.signals import SignalResult

    return SignalResult(score=score, triggered=True, evidence={})


def test_composite_score_weighted_average():
    results = {"a": make_result(80.0), "b": make_result(40.0)}
    assert composite_score(results, {"a": 2.0, "b": 1.0}) == pytest.approx(66.7, abs=0.05)


def test_composite_score_renormalizes_over_answering_signals():
    results = {"a": make_result(80.0)}
    assert composite_score(results, {"a": 1.0, "missing": 5.0}) == 80.0


def test_composite_score_ignores_zero_weights_and_handles_empty():
    results = {"a": make_result(80.0)}
    assert composite_score(results, {"a": 0.0}) is None
    assert composite_score({}, {"a": 1.0}) is None
