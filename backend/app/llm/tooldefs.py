"""The agent tool registry — defined ONCE, served to every surface (spec §13.2).

In-app chat, Celery research tasks, and the MCP server all consume this list;
they can never drift. Each tool wraps an existing service function; handlers
stay thin (resolve symbol → call service → trim output) and raise
`ToolInputError` for anything the model can fix by retrying with better
arguments.

Schemas are lowest-common-denominator JSON Schema — only type / properties /
required / description / enum / items — because the OpenAI-compatible family
(notably Gemini's endpoint) rejects `default`, `anyOf`, and `$ref`. A unit
test locks this.

Excluded operations (spec §13.2) are structural: the registry simply never
defines them, so no configuration can expose them to any agent surface.
`create_alert_rule` / `delete_alert_rule` from the spec table are deferred
with per-asset price alerts to Phase 7 — no alert-rule model exists yet.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings

# operations that must never exist as agent tools, on any surface, at any tier
EXCLUDED_OPERATIONS = frozenset(
    {
        "update_transaction",
        "delete_transaction",
        "update_llm_settings",
        "update_api_keys",
        "delete_mandate",
        "delete_watchlist",
        "delete_note",
        "update_alert_channels",
        "bulk_delete",
        "execute_trade",
        "place_order",
        "buy",
        "sell",
    }
)

AI_NOTE_FOOTER = "*AI-generated, informational only. Not investment advice.*"

MAX_OHLCV_ROWS = 500
MAX_NEWS_ITEMS = 25
MAX_SCREEN_HITS = 50


class ToolInputError(Exception):
    """The model supplied bad arguments; the message goes back as the tool result."""


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    tier: Literal["read", "write"]
    schema: dict[str, Any]
    handler: Callable[[Session, dict], Any]
    summarize: Callable[[dict, Any], str]


# --- shared helpers -------------------------------------------------------------


def _resolve_asset(session: Session, symbol: str):
    from app.models import Asset

    if not symbol or not str(symbol).strip():
        raise ToolInputError("symbol is required")
    matches = session.scalars(
        select(Asset).where(func.upper(Asset.symbol) == str(symbol).strip().upper())
    ).all()
    if not matches:
        raise ToolInputError(
            f"'{symbol}' is not a tracked asset — use search_assets to find tracked symbols"
        )
    if len(matches) > 1:
        classes = sorted(a.asset_class for a in matches)
        raise ToolInputError(f"symbol '{symbol}' is ambiguous across {classes}")
    return matches[0]


def _round(value: Any, digits: int = 4) -> Any:
    if isinstance(value, float):
        return round(value, digits)
    return value


def _http_detail(exc) -> str:
    detail = getattr(exc, "detail", None)
    return detail if isinstance(detail, str) else repr(detail)


# --- read tier -------------------------------------------------------------------


def _search_assets(session: Session, args: dict) -> Any:
    from app.models import Asset

    query = str(args["query"]).strip()
    if not query:
        raise ToolInputError("query must not be empty")
    rows = session.scalars(
        select(Asset)
        .where(Asset.is_active.is_(True))
        .where((Asset.symbol.ilike(f"%{query}%")) | (Asset.name.ilike(f"%{query}%")))
        .order_by(func.length(Asset.symbol))
        .limit(10)
    ).all()
    return {
        "results": [
            {
                "symbol": a.symbol,
                "name": a.name,
                "asset_class": a.asset_class,
                "currency": a.currency,
            }
            for a in rows
        ]
    }


def _get_asset_overview(session: Session, args: dict) -> Any:
    from app.models import AssetMetrics

    asset = _resolve_asset(session, args["symbol"])
    metrics = session.get(AssetMetrics, asset.id)
    if metrics is None:
        return {
            "symbol": asset.symbol,
            "name": asset.name,
            "asset_class": asset.asset_class,
            "metrics": None,
            "note": "no metrics snapshot yet — the nightly refresh has not covered this asset",
        }
    from app.models.asset_metrics import METRIC_COLUMNS

    values = {
        column: _round(getattr(metrics, column))
        for column in METRIC_COLUMNS
        if getattr(metrics, column) is not None
    }
    return {
        "symbol": asset.symbol,
        "name": asset.name,
        "asset_class": asset.asset_class,
        "currency": asset.currency,
        "as_of": metrics.as_of.isoformat(),
        "metrics": values,
    }


def _get_ohlcv(session: Session, args: dict) -> Any:
    from app.analysis.data import load_ohlcv_frame

    asset = _resolve_asset(session, args["symbol"])
    interval = args.get("interval") or "1d"
    if interval not in ("1d", "1w", "1M"):
        raise ToolInputError("interval must be one of 1d, 1w, 1M")
    lookback = int(args.get("lookback_days") or 120)
    lookback = max(5, min(lookback, 1825))
    df = load_ohlcv_frame(session, asset.id, interval=interval, lookback_days=lookback)
    if df.empty:
        return {"symbol": asset.symbol, "interval": interval, "candles": []}
    df = df.tail(MAX_OHLCV_ROWS)
    candles = [
        [
            ts.date().isoformat(),
            _round(float(row.open)),
            _round(float(row.high)),
            _round(float(row.low)),
            _round(float(row.close)),
            None if row.volume is None else float(row.volume),
        ]
        for ts, row in df.iterrows()
    ]
    return {
        "symbol": asset.symbol,
        "interval": interval,
        "columns": ["date", "open", "high", "low", "close", "volume"],
        "candles": candles,
    }


def _get_fundamentals(session: Session, args: dict) -> Any:
    from app.models import Fundamentals
    from app.models.fundamentals import FUNDAMENTAL_COLUMNS

    asset = _resolve_asset(session, args["symbol"])
    if asset.asset_class not in ("stock", "etf"):
        raise ToolInputError("fundamentals are only available for stocks and ETFs")
    rows = session.scalars(
        select(Fundamentals)
        .where(Fundamentals.asset_id == asset.id, Fundamentals.period == "annual")
        .order_by(Fundamentals.report_date.desc())
        .limit(6)
    ).all()
    if not rows:
        return {
            "symbol": asset.symbol,
            "annual": [],
            "note": "no fundamentals yet — coverage rotates weekly on the free data tier",
        }
    annual = [
        {
            "report_date": row.report_date.isoformat(),
            "fiscal_year": row.fiscal_year,
            **{
                column: _round(getattr(row, column))
                for column in FUNDAMENTAL_COLUMNS
                if getattr(row, column) is not None
            },
        }
        for row in rows
    ]
    return {"symbol": asset.symbol, "annual": annual}


def _get_news(session: Session, args: dict) -> Any:
    from app.models import NewsItem

    asset = _resolve_asset(session, args["symbol"])
    days = max(1, min(int(args.get("days") or 7), 90))
    since = datetime.now(UTC) - timedelta(days=days)
    rows = session.scalars(
        select(NewsItem)
        .where(NewsItem.tickers.contains([asset.symbol]), NewsItem.ts >= since)
        .order_by(NewsItem.ts.desc())
        .limit(MAX_NEWS_ITEMS)
    ).all()
    return {
        "symbol": asset.symbol,
        "days": days,
        "headlines": [
            {
                "ts": item.ts.isoformat(),
                "source": item.source,
                "headline": item.headline,
                "sentiment": item.sentiment,
            }
            for item in rows
        ],
    }


def _run_screen(session: Session, args: dict) -> Any:
    from app.screener.ast import SCREEN_FIELDS, AstError, parse_ast
    from app.screener.sql import run_screen

    asset_class = args.get("asset_class")
    if asset_class not in ("stock", "etf", "crypto", "forex"):
        raise ToolInputError("asset_class must be one of stock, etf, crypto, forex")
    try:
        node = parse_ast(args["filter_ast"], allowed_fields=SCREEN_FIELDS)
    except AstError as exc:
        raise ToolInputError(f"invalid filter_ast: {exc.errors}") from exc
    limit = max(1, min(int(args.get("limit") or 20), MAX_SCREEN_HITS))
    hits = run_screen(session, node, asset_class, limit=limit)
    return {
        "matches": [
            {
                "symbol": hit.symbol,
                "name": hit.name,
                "as_of": hit.as_of.isoformat() if hit.as_of else None,
                "values": {k: _round(v) for k, v in hit.values.items()},
            }
            for hit in hits
        ],
        "count": len(hits),
    }


def _backtest_signal(session: Session, args: dict) -> Any:
    from app.analysis.data import load_ohlcv_frame
    from app.backtest.strategy import build_signal_frame, parse_condition
    from app.discovery.context import history_check
    from app.screener.ast import AstError

    asset = _resolve_asset(session, args["symbol"])
    try:
        entry = parse_condition(args["entry_condition"])
        # an always-false exit keeps build_signal_frame happy; only entries matter here
        exit_ = parse_condition({"field": "close", "op": "<", "value": -1})
    except AstError as exc:
        raise ToolInputError(f"invalid entry_condition: {exc.errors}") from exc

    df = load_ohlcv_frame(session, asset.id, lookback_days=1825)
    if len(df) < 60:
        raise ToolInputError(f"not enough price history for {asset.symbol} (need 60+ bars)")
    frame = build_signal_frame(df, entry, exit_)
    mask = frame["entry_sig"].astype(bool)
    result = history_check(frame["close"], mask)
    return {
        "symbol": asset.symbol,
        "bars_analyzed": int(len(frame)),
        "past_triggers": result["n_triggers"],
        "forward_returns_after_trigger": result["fwd"],
        "note": (
            "median forward return and win rate over 5/20/60 trading days after each "
            "past onset of this condition — history, not a promise"
        ),
    }


def _get_candidates(session: Session, args: dict) -> Any:
    from app.models import Asset, Candidate, Mandate

    stmt = (
        select(Candidate, Asset.symbol, Mandate.name.label("mandate_name"))
        .join(Asset, Asset.id == Candidate.asset_id)
        .join(Mandate, Mandate.id == Candidate.mandate_id)
        .order_by(Candidate.score.desc())
        .limit(max(1, min(int(args.get("limit") or 20), 100)))
    )
    status = args.get("status")
    if status:
        if status not in ("new", "reviewed", "starred", "dismissed"):
            raise ToolInputError("status must be one of new, reviewed, starred, dismissed")
        stmt = stmt.where(Candidate.status == status)
    if args.get("mandate_id"):
        stmt = stmt.where(Candidate.mandate_id == int(args["mandate_id"]))
    rows = session.execute(stmt).all()
    return {
        "candidates": [
            {
                "id": candidate.id,
                "symbol": symbol,
                "mandate": mandate_name,
                "score": _round(candidate.score, 1),
                "status": candidate.status,
                "created_at": candidate.created_at.isoformat(),
                "signals": [
                    {
                        "key": s.get("key"),
                        "score": _round(s.get("score"), 1),
                        "triggered": s.get("triggered"),
                    }
                    for s in (candidate.signals or [])
                ],
            }
            for candidate, symbol, mandate_name in rows
        ]
    }


def _get_mandates(session: Session, args: dict) -> Any:
    from app.models import Mandate

    rows = session.scalars(select(Mandate).order_by(Mandate.name)).all()
    return {
        "mandates": [
            {
                "id": m.id,
                "name": m.name,
                "description": m.description,
                "asset_class": m.asset_class,
                "universe_def": m.universe_def,
                "rules": m.rules,
                "schedule": m.schedule,
                "score_weights": m.score_weights,
                "min_score": m.min_score,
                "active": m.active,
                "last_run_at": m.last_run_at.isoformat() if m.last_run_at else None,
            }
            for m in rows
        ]
    }


def _get_portfolio_positions(session: Session, args: dict) -> Any:
    from datetime import date

    from app.portfolio.fx import SUPPORTED_CURRENCIES
    from app.portfolio.valuation import compute_positions

    currency = (args.get("currency") or get_settings().base_currency).upper()
    if currency not in SUPPORTED_CURRENCIES:
        raise ToolInputError(f"currency must be one of {list(SUPPORTED_CURRENCIES)}")
    report = compute_positions(session, as_of=date.today(), currency=currency)
    # trim to what a research conversation needs
    return {
        "as_of": str(report["as_of"]),
        "currency": report["currency"],
        "totals": {k: _round(v, 2) for k, v in report["totals"].items()},
        "positions": [
            {
                "symbol": p["symbol"],
                "account": p["account_name"],
                "quantity": p["quantity"],
                "value": _round(p["value"], 2),
                "cost_basis": _round(p["cost_basis"], 2),
                "unrealized_pnl": _round(p["unrealized_pnl"], 2),
                "unrealized_pnl_pct": _round(p["unrealized_pnl_pct"]),
                "weight": _round(p["weight"]),
            }
            for p in report["positions"]
        ],
        "cash": [
            {
                "account": c["account_name"],
                "currency": c["currency"],
                "value": _round(c["value"], 2),
            }
            for c in report["cash"]
        ],
        "bank_investments": [
            {
                "name": b["name"],
                "kind": b["kind"],
                "value": _round(b["value"], 2),
                "maturity_date": str(b["maturity_date"]) if b["maturity_date"] else None,
            }
            for b in report["bank_investments"]
        ],
        "warnings": report["warnings"],
    }


def _get_portfolio_performance(session: Session, args: dict) -> Any:
    from app.portfolio.fx import SUPPORTED_CURRENCIES
    from app.portfolio.valuation import performance_report

    period = args.get("period") or "1y"
    if period not in ("1m", "3m", "6m", "ytd", "1y", "all"):
        raise ToolInputError("period must be one of 1m, 3m, 6m, ytd, 1y, all")
    currency = (args.get("currency") or get_settings().base_currency).upper()
    if currency not in SUPPORTED_CURRENCIES:
        raise ToolInputError(f"currency must be one of {list(SUPPORTED_CURRENCIES)}")
    report = performance_report(session, period=period, currency=currency)
    return {
        "period": report["period"],
        "currency": report["currency"],
        "start": str(report["start"]),
        "end": str(report["end"]),
        "time_weighted_return": _round(report["twr"]),
        "time_weighted_return_annualized": _round(report["twr_annualized"]),
        "money_weighted_return_irr": _round(report["irr"]),
        "benchmark": (
            {"symbol": report["benchmark"]["symbol"]} if report.get("benchmark") else None
        ),
    }


def _get_ingestion_status(session: Session, args: dict) -> Any:
    from app.models import IngestionRun

    rows = session.scalars(
        select(IngestionRun).order_by(IngestionRun.started_at.desc()).limit(10)
    ).all()
    return {
        "recent_runs": [
            {
                "job": run.job_name,
                "provider": run.provider,
                "asset_class": run.asset_class,
                "status": run.status,
                "rows_written": run.rows_written,
                "symbols_ok": run.symbols_ok,
                "symbols_failed": run.symbols_failed,
                "started_at": run.started_at.isoformat(),
            }
            for run in rows
        ]
    }


# --- write tier ------------------------------------------------------------------


def _write_research_note(session: Session, args: dict) -> Any:
    from app.models import AssetNote

    asset = _resolve_asset(session, args["symbol"])
    markdown = str(args["markdown"]).strip()
    if not markdown:
        raise ToolInputError("markdown must not be empty")
    if len(markdown) > 100_000:
        raise ToolInputError("note is too long (100k character limit)")
    if AI_NOTE_FOOTER not in markdown:
        markdown = f"{markdown}\n\n---\n{AI_NOTE_FOOTER}"
    note = AssetNote(
        asset_id=asset.id,
        title=str(args.get("title") or "").strip() or None,
        body_md=markdown,
        source="ai",
    )
    session.add(note)
    session.flush()
    return {"note_id": note.id, "symbol": asset.symbol, "labeled": "ai"}


def _manage_watchlist(session: Session, args: dict) -> Any:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.models import Watchlist, WatchlistItem

    action = args.get("action")
    if action not in ("add", "remove"):
        raise ToolInputError("action must be 'add' or 'remove'")
    asset = _resolve_asset(session, args["symbol"])
    watchlist_name = str(args.get("watchlist") or "Default")
    watchlist = session.scalar(select(Watchlist).where(Watchlist.name == watchlist_name))
    if watchlist is None:
        names = session.scalars(select(Watchlist.name)).all()
        raise ToolInputError(f"watchlist '{watchlist_name}' not found — existing: {list(names)}")

    if action == "add":
        session.execute(
            pg_insert(WatchlistItem.__table__)
            .values(watchlist_id=watchlist.id, asset_id=asset.id)
            .on_conflict_do_nothing(index_elements=["watchlist_id", "asset_id"])
        )
    else:
        item = session.get(WatchlistItem, (watchlist.id, asset.id))
        if item is None:
            raise ToolInputError(f"{asset.symbol} is not on '{watchlist_name}'")
        session.delete(item)
    session.flush()
    return {"action": action, "symbol": asset.symbol, "watchlist": watchlist_name}


def _create_mandate(session: Session, args: dict) -> Any:
    from fastapi import HTTPException

    from app.api.routes.mandates import _apply, _validate_or_422
    from app.models import Mandate
    from app.schemas.discovery import MandateIn

    try:
        body = MandateIn(**args["spec"])
    except ValidationError as exc:
        raise ToolInputError(f"invalid mandate spec: {exc}") from exc
    try:
        _validate_or_422(session, body)
    except HTTPException as exc:
        raise ToolInputError(f"invalid mandate spec: {_http_detail(exc)}") from exc
    if session.scalar(select(Mandate).where(Mandate.name == body.name)) is not None:
        raise ToolInputError(f"a mandate named '{body.name}' already exists")
    mandate = Mandate()
    _apply(mandate, body)
    session.add(mandate)
    session.flush()
    return {"mandate_id": mandate.id, "name": mandate.name, "active": mandate.active}


def _update_mandate(session: Session, args: dict) -> Any:
    from fastapi import HTTPException

    from app.api.routes.mandates import _apply, _validate_or_422
    from app.models import Mandate
    from app.schemas.discovery import MandateIn

    mandate = session.get(Mandate, int(args["mandate_id"]))
    if mandate is None:
        raise ToolInputError("mandate not found — call get_mandates for valid ids")
    patch = dict(args.get("patch") or {})
    if not patch:
        raise ToolInputError("patch must contain at least one field to change")
    current = {
        "name": mandate.name,
        "description": mandate.description,
        "asset_class": mandate.asset_class,
        "universe_def": mandate.universe_def,
        "rules": mandate.rules,
        "schedule": mandate.schedule,
        "score_weights": mandate.score_weights,
        "min_score": mandate.min_score,
        "notify_min_score": mandate.notify_min_score,
        "max_candidates": mandate.max_candidates,
        "cooldown_days": mandate.cooldown_days,
        "notify": mandate.notify,
        "active": mandate.active,
    }
    unknown = set(patch) - set(current)
    if unknown:
        raise ToolInputError(f"unknown patch fields: {sorted(unknown)}")
    try:
        body = MandateIn(**{**current, **patch})
    except ValidationError as exc:
        raise ToolInputError(f"invalid patch: {exc}") from exc
    try:
        _validate_or_422(session, body)
    except HTTPException as exc:
        raise ToolInputError(f"invalid patch: {_http_detail(exc)}") from exc
    _apply(mandate, body)
    session.flush()
    return {"mandate_id": mandate.id, "changed": sorted(patch), "active": mandate.active}


def _trigger_scan(session: Session, args: dict) -> Any:
    from app.models import Mandate, Scan

    mandate = session.get(Mandate, int(args["mandate_id"]))
    if mandate is None:
        raise ToolInputError("mandate not found — call get_mandates for valid ids")
    in_flight = session.scalar(
        select(func.count())
        .select_from(Scan)
        .where(Scan.mandate_id == mandate.id, Scan.status.in_(("queued", "running")))
    )
    if in_flight:
        raise ToolInputError("a scan for this mandate is already queued or running")
    scan = Scan(mandate_id=mandate.id)
    session.add(scan)
    session.flush()
    from worker.tasks import run_scan

    run_scan.delay(scan.id)
    return {"scan_id": scan.id, "mandate": mandate.name, "status": "queued"}


def _update_candidate(session: Session, args: dict) -> Any:
    from app.models import Asset, Candidate

    candidate = session.get(Candidate, int(args["candidate_id"]))
    if candidate is None:
        raise ToolInputError("candidate not found — call get_candidates for valid ids")
    status = args.get("status")
    if status not in ("new", "reviewed", "starred", "dismissed"):
        raise ToolInputError("status must be one of new, reviewed, starred, dismissed")
    candidate.status = status
    session.flush()
    asset = session.get(Asset, candidate.asset_id)
    return {
        "candidate_id": candidate.id,
        "symbol": asset.symbol if asset else None,
        "status": candidate.status,
    }


def _run_strategy_backtest(session: Session, args: dict) -> Any:
    from app.backtest.strategy import STRATEGY_FIELDS, parse_condition
    from app.models import Backtest
    from app.schemas.backtest import StrategyBacktestIn
    from app.screener.ast import AstError

    spec = dict(args["spec"])
    if "symbol" in spec and "asset_id" not in spec:
        spec["asset_id"] = _resolve_asset(session, spec.pop("symbol")).id
    try:
        body = StrategyBacktestIn(**spec)
    except ValidationError as exc:
        raise ToolInputError(f"invalid backtest spec: {exc}") from exc
    from app.models import Asset

    asset = session.get(Asset, body.asset_id)
    if asset is None:
        raise ToolInputError("asset not found — pass a tracked symbol")
    for context, condition in (("entry", body.entry), ("exit", body.exit)):
        try:
            parse_condition(condition)
        except AstError as exc:
            raise ToolInputError(
                f"invalid {context} condition: {exc.errors}; "
                f"valid fields: {sorted(STRATEGY_FIELDS)}"
            ) from exc
    params = {
        "asset_id": body.asset_id,
        "symbol": asset.symbol,
        "entry": body.entry,
        "exit": body.exit,
        "stop_loss_pct": body.stop_loss_pct,
        "take_profit_pct": body.take_profit_pct,
        "position_size_pct": body.position_size_pct,
        "cash": body.cash,
        "fees_pct": body.fees_pct,
        "start": body.start.isoformat() if body.start else None,
        "end": body.end.isoformat() if body.end else None,
    }
    backtest = Backtest(kind="strategy", params=params)
    session.add(backtest)
    session.flush()
    from worker.tasks import run_backtest

    run_backtest.delay(backtest.id)
    return {
        "backtest_id": backtest.id,
        "symbol": asset.symbol,
        "status": "queued",
        "note": "poll the Backtests page or ask again in a minute for results",
    }


def _add_transaction(session: Session, args: dict) -> Any:
    from fastapi import HTTPException

    from app.api.routes.transactions import (
        _apply,
        _validate_ledger_write,
        _validate_or_422,
    )
    from app.models import Account, Transaction
    from app.schemas.portfolio import TransactionIn

    record = dict(args["record"])
    if "symbol" in record and "asset_id" not in record:
        record["asset_id"] = _resolve_asset(session, record.pop("symbol")).id
    if "account" in record and "account_id" not in record:
        account_name = str(record.pop("account"))
        account = session.scalar(select(Account).where(Account.name == account_name))
        if account is None:
            names = session.scalars(select(Account.name)).all()
            raise ToolInputError(f"account '{account_name}' not found — existing: {list(names)}")
        record["account_id"] = account.id
    try:
        body = TransactionIn(**record)
    except ValidationError as exc:
        raise ToolInputError(f"invalid transaction: {exc}") from exc
    try:
        _validate_or_422(session, body)
        _validate_ledger_write(session, body)
    except HTTPException as exc:
        raise ToolInputError(f"invalid transaction: {_http_detail(exc)}") from exc
    txn = Transaction()
    _apply(txn, body)
    session.add(txn)
    session.flush()
    return {
        "transaction_id": txn.id,
        "type": txn.type,
        "quantity": float(txn.quantity),
        "currency": txn.currency,
        "note": "create-only: the agent can never edit or delete transactions",
    }


# --- schemas + registry -----------------------------------------------------------


def _obj(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required}


def _screen_grammar() -> str:
    from app.models.asset_metrics import METRIC_COLUMNS

    return (
        'Filter AST: {"all": [conditions]} / {"any": [...]} / {"not": condition} / '
        '{"field", "op", "value"}. Ops: > < >= <= == != between is_null not_null. '
        'value may be a number, [low, high] for between, or {"field": "other_column"} '
        "to compare columns. Fields: " + ", ".join(sorted(METRIC_COLUMNS))
    )


def _strategy_grammar() -> str:
    from app.backtest.strategy import STRATEGY_FIELDS

    return (
        "Condition AST like the screener plus crosses_above / crosses_below "
        '(e.g. {"field": "sma_50", "op": "crosses_above", "value": {"field": "sma_200"}}). '
        "Daily bars only. Fields: " + ", ".join(sorted(STRATEGY_FIELDS))
    )


def _build_tools() -> dict[str, ToolDef]:
    tools = [
        ToolDef(
            name="search_assets",
            description=(
                "Look up tracked assets by symbol or name fragment. Returns up to 10 "
                "matches with their asset class and currency."
            ),
            tier="read",
            schema=_obj({"query": {"type": "string", "description": "symbol or name fragment"}},
                        ["query"]),
            handler=_search_assets,
            summarize=lambda args, result: (
                f"searched '{args.get('query')}' → {len(result['results'])} matches"
            ),
        ),
        ToolDef(
            name="get_asset_overview",
            description=(
                "Latest indicator/metrics snapshot for one asset: price, returns, "
                "moving averages, RSI, volatility, valuation ratios — everything the "
                "nightly refresh computes."
            ),
            tier="read",
            schema=_obj({"symbol": {"type": "string"}}, ["symbol"]),
            handler=_get_asset_overview,
            summarize=lambda args, result: f"overview for {result.get('symbol')}",
        ),
        ToolDef(
            name="get_ohlcv",
            description="Daily/weekly/monthly candles as compact rows (max 500).",
            tier="read",
            schema=_obj(
                {
                    "symbol": {"type": "string"},
                    "interval": {"type": "string", "enum": ["1d", "1w", "1M"]},
                    "lookback_days": {
                        "type": "integer",
                        "description": "how far back to load (default 120, max 1825)",
                    },
                },
                ["symbol"],
            ),
            handler=_get_ohlcv,
            summarize=lambda args, result: (
                f"{len(result['candles'])} {result['interval']} candles for {result['symbol']}"
            ),
        ),
        ToolDef(
            name="get_fundamentals",
            description=(
                "Annual fundamentals history (up to 6 years): revenue, EPS, free cash "
                "flow, margins, ROE, debt/equity, valuation ratios. Stocks/ETFs only."
            ),
            tier="read",
            schema=_obj({"symbol": {"type": "string"}}, ["symbol"]),
            handler=_get_fundamentals,
            summarize=lambda args, result: (
                f"{len(result.get('annual', []))} annual reports for {result.get('symbol')}"
            ),
        ),
        ToolDef(
            name="get_news",
            description="Recent headlines for an asset (max 25), with source and sentiment.",
            tier="read",
            schema=_obj(
                {
                    "symbol": {"type": "string"},
                    "days": {
                        "type": "integer",
                        "description": "lookback window, default 7, max 90",
                    },
                },
                ["symbol"],
            ),
            handler=_get_news,
            summarize=lambda args, result: (
                f"{len(result['headlines'])} headlines for {result['symbol']} "
                f"({result['days']}d)"
            ),
        ),
        ToolDef(
            name="run_screen",
            description=(
                "Run an ad-hoc screen over the latest metrics snapshot. " + _screen_grammar()
            ),
            tier="read",
            schema=_obj(
                {
                    "filter_ast": {"type": "object", "description": "the filter AST"},
                    "asset_class": {
                        "type": "string",
                        "enum": ["stock", "etf", "crypto", "forex"],
                    },
                    "limit": {"type": "integer", "description": "max matches, default 20"},
                },
                ["filter_ast", "asset_class"],
            ),
            handler=_run_screen,
            summarize=lambda args, result: (
                f"screen over {args.get('asset_class')} → {result['count']} matches"
            ),
        ),
        ToolDef(
            name="backtest_signal",
            description=(
                "Quick signal check: how did this asset move 5/20/60 trading days after "
                "past occurrences of a condition? Median return and win rate per horizon. "
                + _strategy_grammar()
            ),
            tier="read",
            schema=_obj(
                {
                    "symbol": {"type": "string"},
                    "entry_condition": {"type": "object", "description": "the condition AST"},
                },
                ["symbol", "entry_condition"],
            ),
            handler=_backtest_signal,
            summarize=lambda args, result: (
                f"signal check on {result['symbol']}: {result['past_triggers']} past triggers"
            ),
        ),
        ToolDef(
            name="get_candidates",
            description=(
                "Read the Research Inbox: scored opportunities produced by discovery "
                "mandates, best first."
            ),
            tier="read",
            schema=_obj(
                {
                    "status": {
                        "type": "string",
                        "enum": ["new", "reviewed", "starred", "dismissed"],
                    },
                    "mandate_id": {"type": "integer"},
                    "limit": {"type": "integer"},
                },
                [],
            ),
            handler=_get_candidates,
            summarize=lambda args, result: f"{len(result['candidates'])} inbox candidates",
        ),
        ToolDef(
            name="get_mandates",
            description=(
                "List discovery mandates with their universe, rules, schedule, and "
                "signal weights."
            ),
            tier="read",
            schema=_obj({}, []),
            handler=_get_mandates,
            summarize=lambda args, result: f"{len(result['mandates'])} mandates",
        ),
        ToolDef(
            name="get_portfolio_positions",
            description=(
                "Current holdings with market value, cost basis, unrealized P&L, and "
                "weights, plus cash and bank investments. Optional currency (USD/MXN/EUR)."
            ),
            tier="read",
            schema=_obj({"currency": {"type": "string", "enum": ["USD", "MXN", "EUR"]}}, []),
            handler=_get_portfolio_positions,
            summarize=lambda args, result: (
                f"{len(result['positions'])} positions, total "
                f"{result['totals'].get('value')} {result['currency']}"
            ),
        ),
        ToolDef(
            name="get_portfolio_performance",
            description=(
                "Portfolio performance for a period: time-weighted return (annualized "
                "when the window allows) and money-weighted return (IRR)."
            ),
            tier="read",
            schema=_obj(
                {
                    "period": {
                        "type": "string",
                        "enum": ["1m", "3m", "6m", "ytd", "1y", "all"],
                    },
                    "currency": {"type": "string", "enum": ["USD", "MXN", "EUR"]},
                },
                [],
            ),
            handler=_get_portfolio_performance,
            summarize=lambda args, result: (
                f"performance {result['period']}: TWR {result['time_weighted_return']}"
            ),
        ),
        ToolDef(
            name="get_ingestion_status",
            description="Health of the data pipeline: the last 10 ingestion runs.",
            tier="read",
            schema=_obj({}, []),
            handler=_get_ingestion_status,
            summarize=lambda args, result: f"{len(result['recent_runs'])} recent runs",
        ),
        # -- write tier --
        ToolDef(
            name="write_research_note",
            description=(
                "Append a markdown research note to an asset, labeled AI-generated. "
                "A disclaimer footer is always attached."
            ),
            tier="write",
            schema=_obj(
                {
                    "symbol": {"type": "string"},
                    "title": {"type": "string"},
                    "markdown": {"type": "string"},
                },
                ["symbol", "markdown"],
            ),
            handler=_write_research_note,
            summarize=lambda args, result: (
                f"wrote AI note #{result['note_id']} on {result['symbol']}"
            ),
        ),
        ToolDef(
            name="manage_watchlist",
            description=(
                "Add or remove one asset on a watchlist (default watchlist: 'Default'). "
                "Watchlists themselves can only be created or deleted in the UI."
            ),
            tier="write",
            schema=_obj(
                {
                    "action": {"type": "string", "enum": ["add", "remove"]},
                    "symbol": {"type": "string"},
                    "watchlist": {"type": "string", "description": "watchlist name"},
                },
                ["action", "symbol"],
            ),
            handler=_manage_watchlist,
            summarize=lambda args, result: (
                f"{result['action']} {result['symbol']} on '{result['watchlist']}'"
            ),
        ),
        ToolDef(
            name="create_mandate",
            description=(
                "Create a standing discovery mandate. spec needs: name, asset_class "
                "(stock|etf|crypto|forex), universe_def (one of {type:'class'} | "
                "{type:'watchlist', watchlist_id} | {type:'market_cap_floor', "
                "min_market_cap} | {type:'top_by_market_cap', count}), schedule "
                "(5-field cron, local time), score_weights ({signal_key: weight}), and "
                "optionally rules (screener AST), min_score (default 40), notify "
                "(off|instant|digest). Signal keys: " + ", ".join(sorted_signal_keys())
            ),
            tier="write",
            schema=_obj(
                {"spec": {"type": "object", "description": "the mandate definition"}},
                ["spec"],
            ),
            handler=_create_mandate,
            summarize=lambda args, result: (
                f"created mandate #{result['mandate_id']} '{result['name']}'"
            ),
        ),
        ToolDef(
            name="update_mandate",
            description=(
                "Patch an existing mandate: any of name, description, rules, schedule, "
                "score_weights, min_score, notify_min_score, max_candidates, "
                "cooldown_days, notify, active (true/false toggles it). Deleting "
                "mandates is UI-only."
            ),
            tier="write",
            schema=_obj(
                {
                    "mandate_id": {"type": "integer"},
                    "patch": {"type": "object", "description": "fields to change"},
                },
                ["mandate_id", "patch"],
            ),
            handler=_update_mandate,
            summarize=lambda args, result: (
                f"updated mandate #{result['mandate_id']}: {', '.join(result['changed'])}"
            ),
        ),
        ToolDef(
            name="trigger_scan",
            description="Run a mandate's scan now instead of waiting for its schedule.",
            tier="write",
            schema=_obj({"mandate_id": {"type": "integer"}}, ["mandate_id"]),
            handler=_trigger_scan,
            summarize=lambda args, result: (
                f"queued scan #{result['scan_id']} for '{result['mandate']}'"
            ),
        ),
        ToolDef(
            name="update_candidate",
            description="Star, dismiss, or mark reviewed an Inbox candidate.",
            tier="write",
            schema=_obj(
                {
                    "candidate_id": {"type": "integer"},
                    "status": {
                        "type": "string",
                        "enum": ["new", "reviewed", "starred", "dismissed"],
                    },
                },
                ["candidate_id", "status"],
            ),
            handler=_update_candidate,
            summarize=lambda args, result: (
                f"candidate #{result['candidate_id']} ({result['symbol']}) → {result['status']}"
            ),
        ),
        ToolDef(
            name="run_strategy_backtest",
            description=(
                "Launch a full strategy backtest job (Backtrader + quantstats report). "
                "spec: symbol (or asset_id), entry and exit condition ASTs, optional "
                "stop_loss_pct, take_profit_pct, position_size_pct, cash, fees_pct, "
                "start, end. Returns a backtest_id to poll. " + _strategy_grammar()
            ),
            tier="write",
            schema=_obj(
                {"spec": {"type": "object", "description": "the backtest definition"}},
                ["spec"],
            ),
            handler=_run_strategy_backtest,
            summarize=lambda args, result: (
                f"queued backtest #{result['backtest_id']} on {result['symbol']}"
            ),
        ),
        ToolDef(
            name="add_transaction",
            description=(
                "Log ONE portfolio transaction (create only — the agent can never edit "
                "or delete them; this never executes a trade anywhere). record: account "
                "(name) or account_id, type (buy|sell|deposit|withdrawal|dividend|"
                "interest|fee|transfer_in|transfer_out), ts (ISO datetime), quantity, "
                "currency, and for asset types symbol + price. Writes are validated by "
                "a strict lot replay (oversells rejected)."
            ),
            tier="write",
            schema=_obj(
                {"record": {"type": "object", "description": "the transaction record"}},
                ["record"],
            ),
            handler=_add_transaction,
            summarize=lambda args, result: (
                f"logged transaction #{result['transaction_id']} ({result['type']})"
            ),
        ),
    ]
    return {tool.name: tool for tool in tools}


def sorted_signal_keys() -> list[str]:
    from app.discovery.signals import SIGNALS

    return sorted(SIGNALS)


TOOLS: dict[str, ToolDef] = _build_tools()

# a registry bug that resurrects an excluded operation should fail at import
assert not EXCLUDED_OPERATIONS & set(TOOLS), "excluded operation registered as a tool"


def tools_for_tier(tier: str) -> list[ToolDef]:
    if tier == "write":
        return list(TOOLS.values())
    return [tool for tool in TOOLS.values() if tool.tier == "read"]
