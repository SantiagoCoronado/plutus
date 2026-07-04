"""Strategy backtest: rule-based entry/exit on a single asset (spec §5.4).

Signals are FULLY precomputed with the app's own indicator engine and handed to
Backtrader as two extra data lines — Backtrader only does order fills and trade
bookkeeping, so chart, screener, and strategy math share one source of truth.

LOOK-AHEAD GUARDRAIL: a signal computed on close(t) is acted on in next() at bar t
and filled by the broker at open(t+1) — Backtrader's default next-bar market
execution. Never enable cheat_on_open/coc. Stop-loss/take-profit are checked on
close(t) and also exit at open(t+1): conservative, no intrabar-touch assumption.
"""

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.analysis.data import load_ohlcv_frame
from app.analysis.indicators import compute_series
from app.backtest.panel import COLUMN_TO_SPEC, WARMUP_CALENDAR_DAYS
from app.backtest.perf import downsample_curve, summary_stats
from app.core.logging import get_logger
from app.models import Asset
from app.screener.ast import (
    CROSS_OPS,
    FieldRef,
    Group,
    Leaf,
    Node,
    Not,
    parse_ast,
    referenced_fields,
)
from app.screener.pandas_eval import _evaluate_leaf

log = get_logger(__name__)

STRATEGY_BASE_FIELDS = ("open", "high", "low", "close", "volume")
STRATEGY_FIELDS: frozenset[str] = frozenset(STRATEGY_BASE_FIELDS) | frozenset(COLUMN_TO_SPEC)


def parse_condition(raw: dict) -> Node:
    """Entry/exit condition: screen ops + crosses_above/crosses_below over series."""
    return parse_ast(raw, allowed_fields=STRATEGY_FIELDS, extra_ops=CROSS_OPS)


def build_signal_frame(df: pd.DataFrame, entry: Node, exit_: Node) -> pd.DataFrame:
    """OHLCV + float entry_sig/exit_sig columns (1.0 = fire), warmup bars never fire."""
    fields = referenced_fields(entry) | referenced_fields(exit_)
    indicator_keys = sorted({COLUMN_TO_SPEC[f] for f in fields if f in COLUMN_TO_SPEC})
    indicator_df = compute_series(df, indicator_keys) if indicator_keys else None

    series_map: dict[str, pd.Series] = {}
    for field in fields:
        if field in STRATEGY_BASE_FIELDS:
            series_map[field] = df[field].astype("Float64")
        elif indicator_df is not None and field in indicator_df:
            series_map[field] = indicator_df[field].astype("Float64")
        else:  # gated indicator (e.g. volume-based on a volume-less frame)
            series_map[field] = pd.Series(pd.NA, index=df.index, dtype="Float64")

    frame = df.copy()
    frame["entry_sig"] = _evaluate_condition(entry, series_map).fillna(False).astype(float)
    frame["exit_sig"] = _evaluate_condition(exit_, series_map).fillna(False).astype(float)
    return frame


def _evaluate_condition(node: Node, series_map: dict[str, pd.Series]) -> pd.Series:
    """pandas_eval semantics (Kleene) plus the series-only cross operators."""
    if isinstance(node, Group):
        masks = [_evaluate_condition(child, series_map) for child in node.children]
        result = masks[0]
        for mask in masks[1:]:
            result = (result & mask) if node.kind == "all" else (result | mask)
        return result
    if isinstance(node, Not):
        return ~_evaluate_condition(node.child, series_map)
    if node.op in CROSS_OPS:
        return _cross_mask(node, series_map)
    return _evaluate_leaf(node, series_map)


def _cross_mask(leaf: Leaf, series_map: dict[str, pd.Series]) -> pd.Series:
    series = series_map[leaf.field]
    if isinstance(leaf.value, FieldRef):
        other = series_map[leaf.value.field]
    else:
        other = pd.Series(float(leaf.value), index=series.index, dtype="Float64")
    prev_series, prev_other = series.shift(1), other.shift(1)
    if leaf.op == "crosses_above":
        crossed = (series > other) & (prev_series <= prev_other)
    else:
        crossed = (series < other) & (prev_series >= prev_other)
    # a cross needs all four values: NaN warmup bars can never fire
    valid = (
        series.notna() & other.notna() & prev_series.notna() & prev_other.notna()
    ).astype("boolean")
    return crossed & valid


def run_strategy_backtest(
    session: Session, params: dict[str, Any], artifact_path: Path
) -> dict[str, Any]:
    """Execute a strategy backtest; returns {stats, equity_curve, trade_list, artifact_path}."""
    asset = session.get(Asset, params["asset_id"])
    if asset is None:
        raise ValueError(f"asset {params['asset_id']} not found")

    entry = parse_condition(params["entry"])
    exit_ = parse_condition(params["exit"])

    start = date.fromisoformat(params["start"]) if params.get("start") else None
    end = date.fromisoformat(params["end"]) if params.get("end") else None
    load_start = start - timedelta(days=WARMUP_CALENDAR_DAYS) if start else None
    df = load_ohlcv_frame(
        session, asset.id, "1d", lookback_days=10_000, start=load_start, end=end
    )
    if df.empty:
        raise ValueError(f"no OHLCV history for {asset.symbol}")

    frame = build_signal_frame(df, entry, exit_)
    if start is not None:  # signals warmed on the full frame, trading clipped to the window
        frame = frame.loc[pd.Timestamp(start, tz="UTC") :]
    if len(frame) < 2:
        raise ValueError("not enough bars in the requested window")

    equity, trades = _run_cerebro(frame, params)

    leg_returns = [t["pnl_pct"] for t in trades if t["pnl_pct"] is not None]
    buy_hold = params["cash"] * frame["close"] / float(frame["close"].iloc[0])
    stats = summary_stats(equity, buy_hold, leg_returns)
    stats["symbol"] = asset.symbol
    stats["benchmark_symbol"] = f"{asset.symbol} buy & hold"

    saved_artifact = _write_quantstats_report(
        equity, buy_hold, artifact_path, title=f"Backtest — {asset.symbol}"
    )

    return {
        "stats": stats,
        "equity_curve": {
            "portfolio": downsample_curve(equity),
            "benchmark": downsample_curve(buy_hold),
        },
        "trade_list": trades,
        "artifact_path": str(saved_artifact) if saved_artifact else None,
    }


def _run_cerebro(frame: pd.DataFrame, params: dict[str, Any]) -> tuple[pd.Series, list[dict]]:
    import backtrader as bt

    class SignalData(bt.feeds.PandasData):
        lines = ("entry_sig", "exit_sig")
        params = (("entry_sig", -1), ("exit_sig", -1))

    class RuleStrategy(bt.Strategy):
        params = (
            ("stop_loss_pct", None),
            ("take_profit_pct", None),
            ("position_size_pct", 100.0),
        )

        def __init__(self):
            self.equity: list[tuple] = []
            self.trades: list[dict] = []
            self._entry_price: float | None = None
            self._entry_size: int | None = None

        def next(self):
            self.equity.append((self.data.datetime.datetime(0), self.broker.getvalue()))
            price = float(self.data.close[0])
            if not self.position:
                if self.data.entry_sig[0] > 0:
                    size = int(
                        self.broker.getvalue() * self.p.position_size_pct / 100.0 / price
                    )
                    if size > 0:
                        self.buy(size=size)  # market order -> fills at open(t+1)
                return
            stop_hit = (
                self.p.stop_loss_pct is not None
                and self._entry_price is not None
                and price <= self._entry_price * (1 - self.p.stop_loss_pct)
            )
            profit_hit = (
                self.p.take_profit_pct is not None
                and self._entry_price is not None
                and price >= self._entry_price * (1 + self.p.take_profit_pct)
            )
            if self.data.exit_sig[0] > 0 or stop_hit or profit_hit:
                self.close()

        def notify_order(self, order):
            if order.status == order.Completed and order.isbuy():
                self._entry_price = float(order.executed.price)
                self._entry_size = int(order.executed.size)

        def notify_trade(self, trade):
            if not trade.isclosed:
                return
            entry_price, size = self._entry_price, self._entry_size
            # gross pnl = (exit - entry) * size  =>  derive the exit fill price
            exit_price = entry_price + trade.pnl / size if entry_price and size else None
            pnl_pct = (
                exit_price / entry_price - 1
                if entry_price and exit_price is not None
                else None
            )
            self.trades.append(
                {
                    "entry_ts": bt.num2date(trade.dtopen).date().isoformat(),
                    "exit_ts": bt.num2date(trade.dtclose).date().isoformat(),
                    "entry_price": round(entry_price, 6) if entry_price else None,
                    "exit_price": round(exit_price, 6) if exit_price is not None else None,
                    "pnl": round(float(trade.pnlcomm), 2),
                    "pnl_pct": round(pnl_pct, 6) if pnl_pct is not None else None,
                    "bars_held": int(trade.barlen),
                }
            )
            self._entry_price = None

    data = frame.copy()
    data.index = data.index.tz_localize(None)  # backtrader dislikes tz-aware indexes
    cerebro = bt.Cerebro()
    cerebro.broker.setcash(params["cash"])
    cerebro.broker.setcommission(commission=params.get("fees_pct", 0.0))
    cerebro.adddata(SignalData(dataname=data))
    cerebro.addstrategy(
        RuleStrategy,
        stop_loss_pct=params.get("stop_loss_pct"),
        take_profit_pct=params.get("take_profit_pct"),
        position_size_pct=params.get("position_size_pct", 100.0),
    )
    strategy = cerebro.run()[0]

    equity = pd.Series(
        [value for _, value in strategy.equity],
        index=pd.DatetimeIndex([ts for ts, _ in strategy.equity], tz="UTC"),
    )
    return equity, strategy.trades


def _write_quantstats_report(
    equity: pd.Series, benchmark_equity: pd.Series, artifact_path: Path, title: str
) -> Path | None:
    """Best-effort HTML artifact; a report failure never fails the run."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import quantstats_lumi as qs

        returns = equity.pct_change().dropna()
        bench = benchmark_equity.pct_change().dropna()
        returns.index = returns.index.tz_localize(None)
        bench.index = bench.index.tz_localize(None)
        qs.reports.html(returns, benchmark=bench, output=str(artifact_path), title=title)
        return artifact_path
    except Exception as exc:  # noqa: BLE001 — the stats/trades are the deliverable
        log.warning("quantstats_report_failed", error=str(exc))
        return None
