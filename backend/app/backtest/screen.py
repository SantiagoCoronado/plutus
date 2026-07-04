"""Screen backtest: replay a saved screen's filter AST through history (spec §5.4).

Semantics
---------
- Universe: all active assets of the run's asset_class (one class per run keeps a
  single trading calendar — no mixed stock/crypto weekday hazards).
- Every `holding_days` bars (from the first eligible bar) the AST is evaluated on
  the point-in-time panel; matching assets get equal target weights, no matches
  means all-cash until the next rebalance.
- NEXT-BAR EXECUTION (look-ahead guardrail): a selection computed on close(t) is
  ordered on bar t+1 and filled at open(t+1). Metrics at t never trade at t.
- Fundamentals fields are rejected upstream (parse_ast with BACKTESTABLE_FIELDS):
  we only store their latest annual snapshot, so screening history on them would
  be silent look-ahead.

vectorbt is confined to the ~20 lines in `_simulate` so a plain-pandas simulator
could replace it; all reported statistics come from perf.py, never pf.stats().
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backtest.panel import WARMUP_BARS, build_field_panel
from app.backtest.perf import downsample_curve, summary_stats
from app.core.config import get_settings
from app.models import Asset
from app.screener.ast import BACKTESTABLE_FIELDS, parse_ast, referenced_fields
from app.screener.pandas_eval import evaluate_mask

INIT_CASH = 100_000.0
MAX_HOLDINGS_LOG = 200


def run_screen_backtest(session: Session, params: dict[str, Any]) -> dict[str, Any]:
    """Execute a screen backtest; returns {stats, equity_curve, trade_list}.

    params: {ast, asset_class, holding_days, start?, end?, benchmark, fees_pct}
    (validated at the API layer; re-parsed here because the worker only gets JSON).
    """
    node = parse_ast(params["ast"], allowed_fields=BACKTESTABLE_FIELDS)
    asset_class: str = params["asset_class"]
    holding_days: int = params.get("holding_days", 20)
    fees_pct: float = params.get("fees_pct", 0.0)
    benchmark_symbol: str = params.get("benchmark") or get_settings().benchmark_stock

    end = date.fromisoformat(params["end"]) if params.get("end") else datetime.now(UTC).date()
    start = (
        date.fromisoformat(params["start"])
        if params.get("start")
        else end - timedelta(days=4 * 365)
    )
    if start >= end:
        raise ValueError(f"start {start} must be before end {end}")

    assets = list(
        session.scalars(
            select(Asset)
            .where(Asset.is_active, Asset.asset_class == asset_class)
            .order_by(Asset.symbol)
        )
    )
    if not assets:
        raise ValueError(f"no active {asset_class} assets to backtest")

    fields = referenced_fields(node)
    panels, close_panel, open_panel = build_field_panel(
        session, assets, fields, start, end, benchmark_symbol
    )
    calendar = close_panel.index
    if len(calendar) <= WARMUP_BARS + 1:
        raise ValueError(
            f"only {len(calendar)} bars of history — need more than {WARMUP_BARS} "
            "(warmup) plus a trading window"
        )

    start_ts = pd.Timestamp(start, tz="UTC")
    first_eligible = max(WARMUP_BARS, int(calendar.searchsorted(start_ts)))
    # bar t+1 must exist for the fill: last rebalance is the second-to-last bar
    rebalance_positions = list(range(first_eligible, len(calendar) - 1, holding_days))
    if not rebalance_positions:
        raise ValueError("no rebalance dates inside the requested window")

    weights, holdings_log, leg_returns = _select_holdings(
        node, panels, close_panel, open_panel, calendar, rebalance_positions, holding_days
    )
    equity = _simulate(close_panel, open_panel, weights, fees_pct)

    benchmark_equity = _benchmark_equity(
        session, benchmark_symbol, calendar, rebalance_positions[0]
    )

    window_equity = equity.iloc[rebalance_positions[0] :]
    stats = summary_stats(window_equity, benchmark_equity, leg_returns)
    stats["universe_size"] = len(assets)
    stats["rebalances"] = len(rebalance_positions)
    stats["holding_days"] = holding_days
    stats["benchmark_symbol"] = benchmark_symbol

    return {
        "stats": stats,
        "equity_curve": {
            "portfolio": downsample_curve(window_equity),
            "benchmark": downsample_curve(benchmark_equity)
            if benchmark_equity is not None
            else [],
        },
        "trade_list": holdings_log[:MAX_HOLDINGS_LOG],
    }


def _select_holdings(
    node,
    panels: dict[str, pd.DataFrame],
    close_panel: pd.DataFrame,
    open_panel: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    rebalance_positions: list[int],
    holding_days: int,
) -> tuple[pd.DataFrame, list[dict], list[float]]:
    """Target weights (placed on bar t+1), a holdings log, and per-leg returns."""
    weights = pd.DataFrame(np.nan, index=calendar, columns=close_panel.columns)
    holdings_log: list[dict] = []
    leg_returns: list[float] = []

    for position in rebalance_positions:
        ts = calendar[position]
        row = {field: panel.loc[ts] for field, panel in panels.items()}
        mask = evaluate_mask(node, row).fillna(False)
        # only tradable names: a real close at t (metrics) and a real open at t+1 (fill)
        tradable = close_panel.loc[ts].notna() & open_panel.iloc[position + 1].notna()
        selected = sorted(mask[mask & tradable].index)

        fill_position = position + 1
        weights.iloc[fill_position] = 0.0
        if selected:
            weights.loc[calendar[fill_position], selected] = 1.0 / len(selected)

        holdings_log.append({"date": ts.date().isoformat(), "symbols": selected})

        # leg return per held asset: open(t+1) -> open(exit+1), matching the fills
        exit_fill = min(position + holding_days + 1, len(calendar) - 1)
        for symbol in selected:
            entry_price = open_panel.iloc[fill_position][symbol]
            exit_price = open_panel.iloc[exit_fill][symbol]
            if pd.notna(entry_price) and pd.notna(exit_price) and entry_price > 0:
                leg_returns.append(float(exit_price / entry_price - 1))

    return weights, holdings_log, leg_returns


def _simulate(
    close_panel: pd.DataFrame,
    open_panel: pd.DataFrame,
    weights: pd.DataFrame,
    fees_pct: float,
) -> pd.Series:
    """Portfolio equity via vectorbt; NaN weight rows mean 'no order this bar'."""
    import vectorbt as vbt

    portfolio = vbt.Portfolio.from_orders(
        close=close_panel,
        size=weights,
        size_type="targetpercent",
        price=open_panel,
        val_price=open_panel,
        group_by=True,
        cash_sharing=True,
        call_seq="auto",
        fees=fees_pct,
        init_cash=INIT_CASH,
        freq="1D",
    )
    return portfolio.value()


def _benchmark_equity(
    session: Session,
    benchmark_symbol: str,
    calendar: pd.DatetimeIndex,
    first_position: int,
) -> pd.Series | None:
    """Buy-and-hold the benchmark from the strategy's first bar, same starting cash."""
    from app.backtest.panel import _load_benchmark_close

    close = _load_benchmark_close(
        session,
        benchmark_symbol,
        calendar[0].date(),
        calendar[-1].date(),
    )
    if close is None:
        return None
    aligned = close.reindex(calendar).ffill().iloc[first_position:].dropna()
    if len(aligned) < 2:
        return None
    return INIT_CASH * aligned / float(aligned.iloc[0])
