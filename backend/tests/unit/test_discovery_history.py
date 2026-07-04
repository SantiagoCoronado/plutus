"""History check: hand-computed forward returns after past trigger onsets."""

import numpy as np
import pandas as pd
import pytest

from app.discovery.context import build_context, chart_payload, history_check
from app.discovery.signals import SignalResult


def series(values) -> pd.Series:
    values = np.asarray(values, dtype=float)
    index = pd.bdate_range(end="2026-07-03", periods=len(values), tz="UTC")
    return pd.Series(values, index=index)


def mask_at(n: int, bars, close: pd.Series) -> pd.Series:
    mask = pd.Series(False, index=close.index)
    mask.iloc[list(bars)] = True
    return mask


def test_forward_returns_hand_computed():
    close = series(100 + np.arange(120))  # close[i] = 100 + i
    mask = mask_at(120, [10, 50], close)
    result = history_check(close, mask)

    assert result["n_triggers"] == 2
    # onset 10: 5d = 115/110-1, 20d = 130/110-1, 60d = 170/110-1
    # onset 50: 5d = 155/150-1, 20d = 170/150-1, 60d = 210/150-1
    assert result["fwd"]["5d"]["n"] == 2
    assert result["fwd"]["5d"]["median"] == pytest.approx(
        np.median([115 / 110 - 1, 155 / 150 - 1]), abs=1e-4
    )
    assert result["fwd"]["60d"]["median"] == pytest.approx(
        np.median([170 / 110 - 1, 210 / 150 - 1]), abs=1e-4
    )
    assert result["fwd"]["20d"]["win_rate"] == 1.0


def test_consecutive_trigger_run_collapses_to_one_onset():
    close = series(100 + np.arange(120))
    mask = mask_at(120, [50, 51, 52], close)
    assert history_check(close, mask)["n_triggers"] == 1


def test_live_trigger_on_final_bar_is_excluded():
    close = series(100 + np.arange(120))
    mask = mask_at(120, [119], close)
    result = history_check(close, mask)
    assert result["n_triggers"] == 0
    assert result["fwd"] == {}


def test_horizons_truncate_near_frame_end():
    close = series(100 + np.arange(120))
    mask = mask_at(120, [110], close)  # only 9 bars of future
    result = history_check(close, mask)
    assert result["n_triggers"] == 1
    assert "5d" in result["fwd"]
    assert "20d" not in result["fwd"]
    assert "60d" not in result["fwd"]


def test_losing_triggers_produce_zero_win_rate():
    close = series(200 - np.arange(120))  # falling market
    mask = mask_at(120, [10, 40], close)
    result = history_check(close, mask)
    assert result["fwd"]["20d"]["win_rate"] == 0.0
    assert result["fwd"]["20d"]["median"] < 0


def test_chart_payload_keeps_precision_and_caps_length():
    close = series(np.linspace(0.1234567, 0.2, 200))
    points = chart_payload(close)
    assert len(points) == 120
    assert points[0][1] != round(points[0][1], 2)  # sub-cent precision preserved
    assert isinstance(points[0][0], str)


def test_build_context_shapes():
    close = series(100 + np.arange(120))
    mask = mask_at(120, [30], close)
    results = {
        "breakout": SignalResult(score=90.0, triggered=True, evidence={}, mask=mask),
        "momentum_rank": SignalResult(score=85.0, triggered=True, evidence={}, mask=None),
        "rsi_extreme": SignalResult(score=10.0, triggered=False, evidence={}, mask=mask),
    }
    metrics = {
        "as_of": pd.Timestamp("2026-07-03").date(),
        "close": 219.0,
        "return_1m": 0.05,
        "return_1y": 0.4,
        "rsi_14": 55.0,
        "market_cap": 1e12,
        "pe": 30.0,
        "dist_52w_high": -0.02,
        "volume": 2_000_000.0,
        "volume_avg_20": 1_000_000.0,
    }
    context = build_context(close, results, metrics)

    # only triggered signals with masks get a history check
    assert set(context["history_check"]) == {"breakout"}
    assert context["snapshot"]["volume_ratio"] == 2.0
    assert context["snapshot"]["as_of"] == "2026-07-03"
    assert len(context["chart"]) == 120


def test_build_context_without_metrics_row():
    close = series(100 + np.arange(120))
    context = build_context(close, {}, None)
    assert context["snapshot"] == {}
    assert context["history_check"] == {}
