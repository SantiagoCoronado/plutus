"""Performance statistics for backtests — plain pandas/numpy, no vectorbt.

Both backtest kinds report through these definitions, so screen and strategy
numbers are directly comparable (and unit-testable against hand math).
"""

import math
from collections.abc import Sequence

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def daily_returns(equity: pd.Series) -> pd.Series:
    return equity.pct_change().dropna()


def cagr(equity: pd.Series) -> float | None:
    if len(equity) < 2:
        return None
    start_value, end_value = float(equity.iloc[0]), float(equity.iloc[-1])
    if start_value <= 0 or end_value <= 0:
        return None
    days = (equity.index[-1] - equity.index[0]).days
    if days <= 0:
        return None
    return (end_value / start_value) ** (365.25 / days) - 1


def sharpe(returns: pd.Series, periods_per_year: int = TRADING_DAYS) -> float | None:
    if len(returns) < 2:
        return None
    std = float(returns.std(ddof=1))
    # epsilon, not == 0: a constant-return series leaves ~1e-18 of float noise
    if math.isnan(std) or std < 1e-12:
        return None
    return float(returns.mean()) / std * math.sqrt(periods_per_year)


def max_drawdown(equity: pd.Series) -> float | None:
    if len(equity) < 2:
        return None
    return float((equity / equity.cummax() - 1).min())


def win_rate(leg_returns: Sequence[float]) -> float | None:
    legs = [r for r in leg_returns if r is not None and not math.isnan(r)]
    if not legs:
        return None
    return sum(1 for r in legs if r > 0) / len(legs)


def total_return(equity: pd.Series) -> float | None:
    if len(equity) < 2 or float(equity.iloc[0]) == 0:
        return None
    return float(equity.iloc[-1] / equity.iloc[0] - 1)


def downsample_curve(equity: pd.Series, max_points: int = 500) -> list[list]:
    """[[iso_date, value], ...] with at most max_points, always keeping both endpoints."""
    if equity.empty:
        return []
    if len(equity) > max_points:
        idx = np.unique(np.linspace(0, len(equity) - 1, max_points).round().astype(int))
        equity = equity.iloc[idx]
    return [[ts.date().isoformat(), round(float(v), 2)] for ts, v in equity.items()]


def summary_stats(
    equity: pd.Series,
    benchmark_equity: pd.Series | None,
    leg_returns: Sequence[float],
) -> dict:
    stats = {
        "cagr": cagr(equity),
        "sharpe": sharpe(daily_returns(equity)),
        "max_drawdown": max_drawdown(equity),
        "win_rate": win_rate(leg_returns),
        "total_return": total_return(equity),
        "n_trades": len(leg_returns),
        "start": equity.index[0].date().isoformat() if len(equity) else None,
        "end": equity.index[-1].date().isoformat() if len(equity) else None,
        "bars": int(len(equity)),
        "benchmark": None,
        "excess_return": None,
    }
    if benchmark_equity is not None and len(benchmark_equity) >= 2:
        bench_total = total_return(benchmark_equity)
        stats["benchmark"] = {
            "cagr": cagr(benchmark_equity),
            "total_return": bench_total,
            "max_drawdown": max_drawdown(benchmark_equity),
        }
        if stats["total_return"] is not None and bench_total is not None:
            stats["excess_return"] = stats["total_return"] - bench_total
    return stats
