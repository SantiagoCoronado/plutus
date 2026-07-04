"""Backtrader on py3.13 canary: signal lines in, one round-trip trade out.

backtrader's last release is from 2023 — this test fails loudly if a Python or
pandas upgrade ever breaks its data feed / broker plumbing.
"""

from datetime import UTC

import numpy as np
import pandas as pd
import pytest

from app.backtest.strategy import _run_cerebro

N = 30


def make_signal_frame() -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=N, freq="D", tz=UTC)
    close = pd.Series(100 + np.arange(N, dtype=float), index=idx)
    frame = pd.DataFrame(
        {
            "open": close.shift(1).fillna(close.iloc[0]),
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": np.full(N, 1e6),
            "entry_sig": np.zeros(N),
            "exit_sig": np.zeros(N),
        },
        index=idx,
    )
    frame.iloc[5, frame.columns.get_loc("entry_sig")] = 1.0
    frame.iloc[20, frame.columns.get_loc("exit_sig")] = 1.0
    return frame


PARAMS = {"cash": 100_000.0, "fees_pct": 0.0, "position_size_pct": 100.0}


class TestCerebro:
    def test_round_trip_trade_next_bar_fills(self):
        equity, trades = _run_cerebro(make_signal_frame(), PARAMS)
        assert len(equity) == N  # next() ran every bar
        assert len(trades) == 1
        trade = trades[0]
        # signal at close(5)=105 -> filled at open(6)=105 ; exit signal at close(20)
        # -> filled at open(21)=120
        assert trade["entry_price"] == pytest.approx(105.0)
        assert trade["exit_price"] == pytest.approx(120.0)
        assert trade["pnl_pct"] == pytest.approx(120 / 105 - 1)
        assert trade["pnl"] > 0 and trade["bars_held"] == 15

    def test_stop_loss_closes_position(self):
        idx = pd.date_range("2025-01-01", periods=N, freq="D", tz=UTC)
        close = pd.Series(
            np.concatenate([[100, 100], np.linspace(100, 70, N - 2)]), index=idx
        )
        frame = pd.DataFrame(
            {
                "open": close.shift(1).fillna(close.iloc[0]),
                "high": close + 1,
                "low": close - 1,
                "close": close,
                "volume": np.full(N, 1e6),
                "entry_sig": np.zeros(N),
                "exit_sig": np.zeros(N),  # never fires — only the stop can exit
            },
            index=idx,
        )
        frame.iloc[1, frame.columns.get_loc("entry_sig")] = 1.0
        equity, trades = _run_cerebro(frame, {**PARAMS, "stop_loss_pct": 0.1})
        assert len(trades) == 1
        assert trades[0]["pnl_pct"] < -0.08  # stopped out near -10%

    def test_position_sizing(self):
        equity, trades = _run_cerebro(
            make_signal_frame(), {**PARAMS, "position_size_pct": 50.0}
        )
        # half the capital in a rising market -> half the gross profit
        full_equity, _ = _run_cerebro(make_signal_frame(), PARAMS)
        half_gain = float(equity.iloc[-1]) - 100_000
        full_gain = float(full_equity.iloc[-1]) - 100_000
        assert half_gain == pytest.approx(full_gain / 2, rel=0.02)
