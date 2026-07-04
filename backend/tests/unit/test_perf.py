"""Backtest stat definitions vs hand-computed references."""

import math

import numpy as np
import pandas as pd
import pytest

from app.backtest.perf import (
    cagr,
    daily_returns,
    downsample_curve,
    max_drawdown,
    sharpe,
    summary_stats,
    total_return,
    win_rate,
)


def equity_series(values, start="2024-01-01"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="D"))


class TestCagr:
    def test_two_points_one_year(self):
        # 100 -> 110 over exactly 365.25 days ~ 10% annualized
        idx = pd.to_datetime(["2024-01-01", "2025-01-01"])  # 366 days (leap year)
        eq = pd.Series([100.0, 110.0], index=idx)
        expected = (110 / 100) ** (365.25 / 366) - 1
        assert cagr(eq) == pytest.approx(expected, abs=1e-12)

    def test_degenerate(self):
        assert cagr(equity_series([100.0])) is None
        assert cagr(equity_series([0.0, 100.0])) is None


class TestSharpe:
    def test_constant_return_has_no_variance(self):
        eq = equity_series([100 * 1.01**i for i in range(10)])
        assert sharpe(daily_returns(eq)) is None  # std == 0

    def test_hand_computed(self):
        returns = pd.Series([0.01, -0.02, 0.03, 0.01])
        expected = returns.mean() / returns.std(ddof=1) * math.sqrt(252)
        assert sharpe(returns) == pytest.approx(expected, abs=1e-12)


class TestMaxDrawdown:
    def test_known_path(self):
        # peak 120, trough 84 -> -30%
        eq = equity_series([100, 120, 96, 84, 110])
        assert max_drawdown(eq) == pytest.approx(84 / 120 - 1, abs=1e-12)

    def test_monotonic_rise_has_zero_dd(self):
        assert max_drawdown(equity_series([1, 2, 3, 4])) == 0.0


class TestWinRate:
    def test_counts_only_positive(self):
        assert win_rate([0.05, -0.02, 0.0, 0.01]) == pytest.approx(0.5)

    def test_ignores_nan_and_none(self):
        assert win_rate([0.05, None, float("nan")]) == 1.0
        assert win_rate([]) is None


class TestDownsample:
    def test_keeps_endpoints_and_caps_length(self):
        eq = equity_series(np.linspace(100, 200, 2000))
        points = downsample_curve(eq, max_points=500)
        assert len(points) <= 500
        assert points[0] == ["2024-01-01", 100.0]
        assert points[-1][1] == 200.0

    def test_short_series_untouched(self):
        assert len(downsample_curve(equity_series([1.0, 2.0, 3.0]))) == 3


class TestSummary:
    def test_shape_and_excess_return(self):
        eq = equity_series([100, 105, 110, 120])
        bench = equity_series([100, 102, 104, 110])
        stats = summary_stats(eq, bench, [0.05, -0.01])
        assert stats["total_return"] == pytest.approx(0.2)
        assert stats["benchmark"]["total_return"] == pytest.approx(0.1)
        assert stats["excess_return"] == pytest.approx(0.1)
        assert stats["n_trades"] == 2 and stats["bars"] == 4

    def test_no_benchmark(self):
        stats = summary_stats(equity_series([100, 110]), None, [])
        assert stats["benchmark"] is None and stats["excess_return"] is None
        assert total_return(equity_series([100, 110])) == pytest.approx(0.1)
