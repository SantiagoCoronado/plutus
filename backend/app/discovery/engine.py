"""The scan funnel: universe -> coarse filter -> signal analysis -> scoring -> candidates.

Fine analysis is DB + pandas only — no provider HTTP ever runs inside a scan
(universe and coarse data come from the nightly asset_metrics snapshot).
"""

from __future__ import annotations

import time
from bisect import bisect_right
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.data import load_ohlcv_frame
from app.core.logging import get_logger
from app.discovery.context import build_context
from app.discovery.signals import (
    MIN_MOMENTUM_PEERS,
    SIGNALS,
    SignalResult,
    composite_score,
)
from app.discovery.universe import resolve_universe
from app.models import METRIC_COLUMNS, AssetMetrics, Candidate, Fundamentals, Mandate
from app.screener.ast import SCREEN_FIELDS, parse_ast
from app.screener.sql import run_screen

log = get_logger(__name__)

# coarse-filter cap: at most this many survivors reach fine analysis
UNIVERSE_CAP = 200
# one global bar window: the full backfill depth, so history checks see 5 years of triggers
LOOKBACK_DAYS = 1825
MOMENTUM_COLUMNS = (AssetMetrics.return_3m, AssetMetrics.return_6m, AssetMetrics.return_1y)


def run_mandate_scan(session: Session, mandate: Mandate, scan_id: int | None = None) -> dict:
    """Run the funnel for one mandate; adds Candidate rows to the session (caller
    commits). Returns {"stats": {...}, "candidates": [Candidate, ...]}."""
    started = time.monotonic()
    weights: dict[str, float] = {
        key: float(w) for key, w in (mandate.score_weights or {}).items() if float(w) > 0
    }
    for key in weights:
        if key not in SIGNALS:
            log.warning("unknown_signal_skipped", mandate_id=mandate.id, signal=key)
    weights = {key: w for key, w in weights.items() if key in SIGNALS}

    universe_ids = resolve_universe(session, mandate)

    if mandate.rules:
        node = parse_ast(mandate.rules, allowed_fields=SCREEN_FIELDS)
        hits = run_screen(
            session, node, mandate.asset_class, limit=UNIVERSE_CAP, asset_ids=universe_ids
        )
        survivor_ids = [hit.asset_id for hit in hits]
    else:
        survivor_ids = universe_ids[:UNIVERSE_CAP]

    momentum = _momentum_context(session, universe_ids)
    metrics_map = _metrics_map(session, survivor_ids)
    fundamentals = (
        _fundamentals_history(session, survivor_ids)
        if weights.keys() & {"valuation_anomaly", "financial_health"}
        and mandate.asset_class == "stock"
        else {}
    )
    quality = (
        _quality_value_context(session, universe_ids)
        if "quality_value" in weights and mandate.asset_class == "stock"
        else {}
    )

    stats: dict[str, Any] = {
        "universe": len(universe_ids),
        "after_rules": len(survivor_ids),
        "analyzed": 0,
        "created": 0,
        "skipped_recent": 0,
        "skipped_no_data": 0,
    }
    as_of: datetime | None = None
    scored: list[tuple[float, int, dict[str, SignalResult], Any]] = []

    for asset_id in survivor_ids:
        frame = load_ohlcv_frame(session, asset_id, lookback_days=LOOKBACK_DAYS)
        if frame.empty:
            stats["skipped_no_data"] += 1
            continue
        stats["analyzed"] += 1
        last_bar = frame.index[-1].to_pydatetime()
        as_of = last_bar if as_of is None else max(as_of, last_bar)

        ctx = dict(momentum.get(asset_id, {}))
        ctx.update(quality.get(asset_id, {}))
        metrics = metrics_map.get(asset_id)
        if metrics is not None:
            ctx["valuation_current"] = {"pe": metrics.get("pe"), "ps": metrics.get("ps")}
        if asset_id in fundamentals:
            rows = fundamentals[asset_id]
            ctx["fundamentals_history"] = rows
            ctx["valuation_history"] = {
                "pe": [row["pe"] for row in rows if row["pe"] is not None],
                "ps": [row["ps"] for row in rows if row["ps"] is not None],
            }

        results: dict[str, SignalResult] = {}
        for key in weights:
            spec = SIGNALS[key]
            if mandate.asset_class not in spec.asset_classes:
                continue
            if len(frame) < spec.min_bars:
                continue
            result = spec.compute(frame, ctx)
            if result is not None:
                results[key] = result

        if not results:
            stats["skipped_no_data"] += 1
            continue
        score = composite_score(results, weights)
        if score is None or score < mandate.min_score:
            continue
        if not any(result.triggered for result in results.values()):
            continue
        scored.append((score, asset_id, results, frame))

    scored.sort(key=lambda item: item[0], reverse=True)
    recent = _latest_candidates(session, mandate, [asset_id for _, asset_id, _, _ in scored])
    now = datetime.now(UTC)
    cooldown_start = now - timedelta(days=mandate.cooldown_days)

    candidates: list[Candidate] = []
    for score, asset_id, results, frame in scored:
        if len(candidates) >= mandate.max_candidates:
            break
        previous = recent.get(asset_id)
        # never stack inbox duplicates; anything within the cooldown was asked and answered
        if previous is not None and (
            previous["status"] == "new" or previous["created_at"] > cooldown_start
        ):
            stats["skipped_recent"] += 1
            continue
        candidate = Candidate(
            mandate_id=mandate.id,
            scan_id=scan_id,
            asset_id=asset_id,
            ts=frame.index[-1].to_pydatetime(),
            score=score,
            signals=_signal_payload(results, weights),
            context=build_context(frame["close"], results, metrics_map.get(asset_id)),
        )
        session.add(candidate)
        candidates.append(candidate)

    stats["created"] = len(candidates)
    stats["as_of"] = as_of.date().isoformat() if as_of else None
    stats["duration_ms"] = int((time.monotonic() - started) * 1000)
    return {"stats": stats, "candidates": candidates}


def _signal_payload(
    results: dict[str, SignalResult], weights: dict[str, float]
) -> list[dict[str, Any]]:
    items = [
        {
            "key": key,
            "label": SIGNALS[key].label,
            "score": result.score,
            "weight": weights[key],
            "triggered": result.triggered,
            "evidence": result.evidence,
        }
        for key, result in results.items()
    ]
    items.sort(key=lambda item: item["weight"] * item["score"], reverse=True)
    return items


def _momentum_context(session: Session, universe_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Cross-sectional momentum percentile vs the mandate's whole universe (not just
    coarse-filter survivors — a rank against a filtered set would be meaningless)."""
    if not universe_ids:
        return {}
    rows = session.execute(
        select(AssetMetrics.asset_id, *MOMENTUM_COLUMNS).where(
            AssetMetrics.asset_id.in_(universe_ids)
        )
    ).all()
    values: dict[int, float] = {}
    for asset_id, *returns in rows:
        available = [r for r in returns if r is not None]
        if len(available) >= 2:
            values[asset_id] = sum(available) / len(available)
    if len(values) < MIN_MOMENTUM_PEERS:
        return {}
    ordered = sorted(values.values())
    top = len(ordered) - 1
    return {
        asset_id: {
            "momentum_percentile": (bisect_right(ordered, value) - 1) / top,
            "momentum_value": value,
            "momentum_peers": len(ordered),
        }
        for asset_id, value in values.items()
    }


def _metrics_map(session: Session, asset_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not asset_ids:
        return {}
    rows = session.scalars(
        select(AssetMetrics).where(AssetMetrics.asset_id.in_(asset_ids))
    ).all()
    return {
        row.asset_id: {
            "as_of": row.as_of,
            **{column: getattr(row, column) for column in METRIC_COLUMNS},
        }
        for row in rows
    }


def _fundamentals_history(session: Session, asset_ids: list[int]) -> dict[int, list[dict]]:
    """Annual fundamentals rows per asset, oldest first — one query feeds both
    valuation_anomaly (pe/ps series) and financial_health (full statements)."""
    if not asset_ids:
        return {}
    rows = session.execute(
        select(
            Fundamentals.asset_id,
            Fundamentals.report_date,
            Fundamentals.fiscal_year,
            Fundamentals.revenue,
            Fundamentals.eps,
            Fundamentals.fcf,
            Fundamentals.gross_margin,
            Fundamentals.net_margin,
            Fundamentals.roe,
            Fundamentals.debt_to_equity,
            Fundamentals.pe,
            Fundamentals.ps,
            Fundamentals.metrics,
        )
        .where(Fundamentals.asset_id.in_(asset_ids), Fundamentals.period == "annual")
        .order_by(Fundamentals.asset_id, Fundamentals.report_date)
    ).all()
    history: dict[int, list[dict]] = {}
    for row in rows:
        history.setdefault(row.asset_id, []).append(
            {
                "report_date": row.report_date,
                "fiscal_year": row.fiscal_year,
                "revenue": row.revenue,
                "eps": row.eps,
                "fcf": row.fcf,
                "gross_margin": row.gross_margin,
                "net_margin": row.net_margin,
                "roe": row.roe,
                "debt_to_equity": row.debt_to_equity,
                "pe": row.pe,
                "ps": row.ps,
                "raw": row.metrics or {},
            }
        )
    return history


def _quality_value_context(
    session: Session, universe_ids: list[int]
) -> dict[int, dict[str, Any]]:
    """Magic-Formula-style context vs the whole universe: percentile-rank
    earnings yield (1/pe from the nightly snapshot) and return on invested
    capital (latest annual statements, roe fallback), then average the two."""
    if not universe_ids:
        return {}
    metric_rows = session.execute(
        select(AssetMetrics.asset_id, AssetMetrics.pe, AssetMetrics.roe).where(
            AssetMetrics.asset_id.in_(universe_ids)
        )
    ).all()

    fundamental_rows = session.execute(
        select(Fundamentals.asset_id, Fundamentals.metrics)
        .where(Fundamentals.asset_id.in_(universe_ids), Fundamentals.period == "annual")
        .order_by(Fundamentals.asset_id, Fundamentals.report_date)
    ).all()
    capital_returns: dict[int, float] = {}
    for asset_id, metrics in fundamental_rows:  # ordered: the last row per asset wins
        value = ((metrics or {}).get("key_metrics") or {}).get("returnOnInvestedCapital")
        if isinstance(value, int | float):
            capital_returns[asset_id] = float(value)

    pairs: dict[int, tuple[float, float]] = {}
    for asset_id, pe, roe in metric_rows:
        if pe is None or pe <= 0:
            continue
        capital_return = capital_returns.get(asset_id, roe)
        if capital_return is None:
            continue
        pairs[asset_id] = (1.0 / pe, float(capital_return))
    if len(pairs) < MIN_MOMENTUM_PEERS:
        return {}

    yields = sorted(ey for ey, _ in pairs.values())
    returns = sorted(roc for _, roc in pairs.values())
    top = len(pairs) - 1
    context: dict[int, dict[str, Any]] = {}
    for asset_id, (earnings_yield, capital_return) in pairs.items():
        yield_pct = (bisect_right(yields, earnings_yield) - 1) / top if top else 1.0
        return_pct = (bisect_right(returns, capital_return) - 1) / top if top else 1.0
        context[asset_id] = {
            "quality_value_percentile": (yield_pct + return_pct) / 2,
            "quality_value_earnings_yield": earnings_yield,
            "quality_value_return_on_capital": capital_return,
            "quality_value_peers": len(pairs),
        }
    return context


def _latest_candidates(
    session: Session, mandate: Mandate, asset_ids: list[int]
) -> dict[int, dict[str, Any]]:
    """Latest existing candidate per (mandate, asset) — the dedup lookup."""
    if not asset_ids:
        return {}
    rows = session.execute(
        select(Candidate.asset_id, Candidate.status, Candidate.created_at)
        .where(Candidate.mandate_id == mandate.id, Candidate.asset_id.in_(asset_ids))
        .order_by(Candidate.asset_id, Candidate.created_at.desc())
    ).all()
    latest: dict[int, dict[str, Any]] = {}
    for asset_id, status, created_at in rows:
        if asset_id not in latest:
            latest[asset_id] = {"status": status, "created_at": created_at}
    return latest
