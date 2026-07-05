"""FX grid alignment — the pure part of app/portfolio/fx.py.

The session-backed lookups (direct/inverse/triangulate) are covered by the
portfolio integration tests against seeded USDMXN closes.
"""

import pandas as pd
import pytest

from app.portfolio.fx import align_to_grid


def closes(points: dict[str, float]) -> pd.Series:
    # tz-aware like ohlcv timestamps
    index = pd.DatetimeIndex(list(points.keys()), tz="UTC")
    return pd.Series(list(points.values()), index=index)


class TestAlignToGrid:
    def test_forward_fills_weekends(self):
        # Fri close carries through Sat/Sun until Mon's close
        series = closes({"2026-01-02": 17.10, "2026-01-05": 17.30})
        grid = pd.date_range("2026-01-02", "2026-01-06", freq="D")
        aligned = align_to_grid(series, grid)
        assert aligned.loc["2026-01-03"] == pytest.approx(17.10)
        assert aligned.loc["2026-01-04"] == pytest.approx(17.10)
        assert aligned.loc["2026-01-05"] == pytest.approx(17.30)

    def test_nan_before_first_close(self):
        series = closes({"2026-01-05": 17.30})
        grid = pd.date_range("2026-01-01", "2026-01-06", freq="D")
        aligned = align_to_grid(series, grid)
        assert aligned.loc["2026-01-02"] != aligned.loc["2026-01-02"]  # NaN
        assert aligned.loc["2026-01-06"] == pytest.approx(17.30)

    def test_lookback_before_grid_start_fills_first_day(self):
        # a close *before* the grid still seeds the fill (the 14-day reachback)
        series = closes({"2025-12-31": 17.00})
        grid = pd.date_range("2026-01-01", "2026-01-03", freq="D")
        aligned = align_to_grid(series, grid)
        assert (aligned == 17.00).all()

    def test_duplicate_days_keep_last(self):
        index = pd.DatetimeIndex(["2026-01-02 00:00", "2026-01-02 12:00"], tz="UTC")
        series = pd.Series([17.0, 17.5], index=index)
        grid = pd.date_range("2026-01-02", "2026-01-02", freq="D")
        assert align_to_grid(series, grid).iloc[0] == pytest.approx(17.5)

    def test_inverse_composition(self):
        # 1/aligned is how fx_series serves the inverse pair
        series = closes({"2026-01-02": 20.0})
        grid = pd.date_range("2026-01-02", "2026-01-03", freq="D")
        inverse = 1.0 / align_to_grid(series, grid)
        assert inverse.loc["2026-01-03"] == pytest.approx(0.05)
