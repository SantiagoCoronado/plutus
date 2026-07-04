"""Screen backtest mechanics: selection, next-bar fills, leg returns, cash fallback.

Uses the pure internals (_select_holdings / _simulate) on hand-built panels so
every expected number is computable by hand; the DB-facing wrapper is covered by
the integration suite.
"""

from datetime import UTC

import numpy as np
import pandas as pd
import pytest

from app.backtest.screen import INIT_CASH, _select_holdings, _simulate
from app.screener.ast import parse_ast

N = 30


@pytest.fixture
def calendar():
    return pd.date_range("2025-01-01", periods=N, freq="D", tz=UTC)


@pytest.fixture
def panels_and_prices(calendar):
    # A trends up, B trends down, C is flat; open(t) == close(t-1) (no gap)
    close = pd.DataFrame(
        {
            "A": 100 + np.arange(N, dtype=float),
            "B": 100 - np.arange(N, dtype=float),
            "C": np.full(N, 100.0),
        },
        index=calendar,
    )
    open_ = close.shift(1)
    open_.iloc[0] = close.iloc[0]
    # a metric we fully control: A always 30 (matches "< 40"), B always 70, C NaN
    rsi = pd.DataFrame(
        {"A": np.full(N, 30.0), "B": np.full(N, 70.0), "C": np.full(N, np.nan)},
        index=calendar,
    )
    return {"rsi_14": rsi}, close, open_


NODE = parse_ast({"field": "rsi_14", "op": "<", "value": 40})


class TestSelection:
    def test_selects_only_matching_with_nan_excluded(self, panels_and_prices, calendar):
        panels, close, open_ = panels_and_prices
        weights, log, legs = _select_holdings(
            NODE, panels, close, open_, calendar, rebalance_positions=[5, 15], holding_days=10
        )
        assert [entry["symbols"] for entry in log] == [["A"], ["A"]]
        # orders land on the NEXT bar (6 and 16), full weight in the single match
        assert weights.iloc[6]["A"] == 1.0 and weights.iloc[6]["B"] == 0.0
        assert weights.iloc[5].isna().all()  # nothing ordered on the signal bar itself

    def test_leg_returns_are_open_to_open(self, panels_and_prices, calendar):
        panels, close, open_ = panels_and_prices
        _, _, legs = _select_holdings(
            NODE, panels, close, open_, calendar, rebalance_positions=[5], holding_days=10
        )
        # entry open(6) = close(5) = 105, exit open(16) = close(15) = 115
        assert legs == [pytest.approx(115 / 105 - 1)]

    def test_no_match_means_all_cash(self, panels_and_prices, calendar):
        panels, close, open_ = panels_and_prices
        node = parse_ast({"field": "rsi_14", "op": ">", "value": 99})
        weights, log, legs = _select_holdings(
            node, panels, close, open_, calendar, rebalance_positions=[5], holding_days=10
        )
        assert log[0]["symbols"] == [] and legs == []
        assert (weights.iloc[6] == 0.0).all()


class TestSimulate:
    def test_equity_tracks_the_held_asset(self, panels_and_prices, calendar):
        panels, close, open_ = panels_and_prices
        weights, _, _ = _select_holdings(
            NODE, panels, close, open_, calendar, rebalance_positions=[5], holding_days=50
        )
        equity = _simulate(close, open_, weights, fees_pct=0.0)
        assert equity.iloc[0] == INIT_CASH
        # bought A at open(6)=105 with 100k -> 952.38 shares; A close(29)=129
        expected_final = INIT_CASH / 105 * 129
        assert float(equity.iloc[-1]) == pytest.approx(expected_final, rel=1e-9)

    def test_all_cash_stays_flat(self, panels_and_prices, calendar):
        panels, close, open_ = panels_and_prices
        node = parse_ast({"field": "rsi_14", "op": ">", "value": 99})
        weights, _, _ = _select_holdings(
            node, panels, close, open_, calendar, rebalance_positions=[5], holding_days=10
        )
        equity = _simulate(close, open_, weights, fees_pct=0.0)
        assert (equity == INIT_CASH).all()

    def test_fees_reduce_equity(self, panels_and_prices, calendar):
        panels, close, open_ = panels_and_prices
        weights, _, _ = _select_holdings(
            NODE, panels, close, open_, calendar, rebalance_positions=[5], holding_days=50
        )
        gross = _simulate(close, open_, weights, fees_pct=0.0)
        net = _simulate(close, open_, weights, fees_pct=0.01)
        assert float(net.iloc[-1]) < float(gross.iloc[-1])
