"""Dashboard aggregate + heatmap (spec §9.1).

Both endpoints compose EXISTING services — valuation/performance for the money
math, ingestion_health for the footer dot, MARKET_STRIP for the pills — so there
is no duplicated cost-basis / TWR / staleness logic here. Everything degrades to
nulls/empty on a fresh install; nothing 500s on an empty database.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import get_settings
from app.health.aggregate import ingestion_health
from app.models import (
    AlertRule,
    Asset,
    AssetMetrics,
    Candidate,
    Mandate,
    Notification,
    Ohlcv,
    Scan,
    WatchlistItem,
)
from app.portfolio.fx import SUPPORTED_CURRENCIES
from app.portfolio.valuation import (
    compute_positions,
    performance_report,
    portfolio_value_series,
)
from app.quotes.subscriptions import MARKET_STRIP
from app.schemas.dashboard import DashboardOut, HeatmapOut

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

# timeframe -> the asset_metrics return column that drives tile coloring. These
# columns are materialized nightly and hold FRACTIONS (0.0123 = +1.23%).
HEATMAP_RETURN_COLUMN = {
    "1D": "return_1d",
    "1W": "return_1w",
    "1M": "return_1m",
    "YTD": "return_ytd",
}


def _currency_or_422(currency: str | None) -> str:
    resolved = (currency or get_settings().base_currency).upper()
    if resolved not in SUPPORTED_CURRENCIES:
        raise HTTPException(
            status_code=422,
            detail={
                "errors": [
                    {
                        "path": "currency",
                        "error": f"unsupported currency (use one of {SUPPORTED_CURRENCIES})",
                    }
                ]
            },
        )
    return resolved


@router.get("", response_model=DashboardOut)
def get_dashboard(db: Session = Depends(get_db), currency: str | None = None):
    ccy = _currency_or_422(currency)
    today = date.today()
    return {
        "portfolio": _portfolio_block(db, today, ccy),
        "ytd": _ytd_block(db, ccy),
        "candidates": _candidates_block(db),
        "last_scan_at": db.scalar(
            select(func.max(Scan.finished_at)).where(Scan.finished_at.is_not(None))
        ),
        "agent_brief": _agent_brief(db),
        "ingestion_status": ingestion_health(db)["status"],
        "armed_alerts": db.scalar(
            select(func.count()).select_from(AlertRule).where(AlertRule.status == "armed")
        )
        or 0,
        "market_strip": [
            {"label": label, "symbol": symbol, "asset_class": asset_class}
            for label, symbol, asset_class in MARKET_STRIP
        ],
    }


@router.get("/heatmap", response_model=HeatmapOut)
def get_heatmap(
    db: Session = Depends(get_db),
    mode: Literal["portfolio", "watchlist", "market"] = Query(default="portfolio"),
    timeframe: Literal["1D", "1W", "1M", "YTD"] = Query(default="1D"),
    currency: str | None = None,
):
    ccy = _currency_or_422(currency)
    column = HEATMAP_RETURN_COLUMN[timeframe]
    if mode == "portfolio":
        tiles = _portfolio_tiles(db, ccy, column)
    else:
        tiles = _universe_tiles(db, column, watchlist_only=(mode == "watchlist"))

    total = sum(tile["size"] for tile in tiles)
    for tile in tiles:
        tile["weight_pct"] = round(tile["size"] / total * 100, 4) if total > 0 else None
    return {"mode": mode, "timeframe": timeframe, "currency": ccy, "tiles": tiles}


# --------------------------------------------------------------------------- #
# /dashboard blocks                                                            #
# --------------------------------------------------------------------------- #


def _portfolio_block(db: Session, today: date, ccy: str) -> dict:
    positions = compute_positions(db, as_of=today, currency=ccy)
    value = positions["totals"]["value"]

    frame = portfolio_value_series(db, start=today - timedelta(days=30), end=today, currency=ccy)
    values, flows = frame["value"], frame["flow"]
    has_data = bool((values > 0).any())

    day_pnl = day_pnl_pct = None
    if has_data and len(values) >= 2:
        last, prev = float(values.iloc[-1]), float(values.iloc[-2])
        # exclude the day's external flow so a deposit doesn't read as a gain
        gain = (last - float(flows.iloc[-1])) - prev
        day_pnl = round(gain, 2)
        day_pnl_pct = round(gain / prev, 6) if prev > 0 else None

    series_30d = (
        [
            {"date": ts.date(), "value": round(float(v), 2)}
            for ts, v in values.items()
        ]
        if has_data
        else []
    )
    return {
        "value": value,
        "currency": ccy,
        "day_pnl": day_pnl,
        "day_pnl_pct": day_pnl_pct,
        "series_30d": series_30d,
    }


def _ytd_block(db: Session, ccy: str) -> dict:
    report = performance_report(db, period="ytd", currency=ccy, benchmark_symbol="SPY")
    return {
        "twr_pct": report["twr"],
        "benchmark_symbol": "SPY",
        # computed independently of the portfolio's funded window so it shows even
        # on a fresh install: first close of the year -> latest close.
        "benchmark_return_pct": _symbol_ytd(db, "SPY"),
    }


def _symbol_ytd(db: Session, symbol: str) -> float | None:
    asset = db.scalar(select(Asset).where(Asset.symbol == symbol))
    if asset is None:
        return None
    year_start = datetime(date.today().year, 1, 1, tzinfo=UTC)
    first = db.scalar(
        select(Ohlcv.close)
        .where(Ohlcv.asset_id == asset.id, Ohlcv.interval == "1d", Ohlcv.ts >= year_start)
        .order_by(Ohlcv.ts)
        .limit(1)
    )
    last = db.scalar(
        select(Ohlcv.close)
        .where(Ohlcv.asset_id == asset.id, Ohlcv.interval == "1d")
        .order_by(Ohlcv.ts.desc())
        .limit(1)
    )
    if first and last and float(first) > 0:
        return round(float(last) / float(first) - 1, 6)
    return None


def _candidates_block(db: Session) -> dict:
    new_count = (
        db.scalar(
            select(func.count()).select_from(Candidate).where(Candidate.status == "new")
        )
        or 0
    )
    rows = db.execute(
        select(
            Candidate,
            Mandate.name.label("mandate_name"),
            Asset.symbol,
            Asset.name.label("asset_name"),
            Asset.asset_class,
        )
        .join(Mandate, Mandate.id == Candidate.mandate_id)
        .join(Asset, Asset.id == Candidate.asset_id)
        .where(Candidate.status != "dismissed")
        .order_by(Candidate.score.desc(), Candidate.id.desc())
        .limit(5)
    ).all()
    top = []
    for row in rows:
        candidate: Candidate = row.Candidate
        triggered = [
            signal["label"]
            for signal in (candidate.signals or [])
            if signal.get("triggered")
        ]
        top.append(
            {
                "id": candidate.id,
                "asset_id": candidate.asset_id,
                "symbol": row.symbol,
                "name": row.asset_name,
                "asset_class": row.asset_class,
                "mandate_name": row.mandate_name,
                "score": candidate.score,
                "status": candidate.status,
                "signals_summary": triggered[:4],
            }
        )
    return {"new_count": new_count, "top": top}


def _agent_brief(db: Session) -> dict | None:
    memo = db.scalar(
        select(Notification)
        .where(Notification.kind == "memo")
        .order_by(Notification.sent_at.desc())
        .limit(1)
    )
    if memo is None:
        return None
    return {
        "subject": memo.subject,
        "body": memo.body,
        "sent_at": memo.sent_at,
        "meta": memo.meta or {},
    }


# --------------------------------------------------------------------------- #
# /dashboard/heatmap tiles                                                     #
# --------------------------------------------------------------------------- #


def _portfolio_tiles(db: Session, ccy: str, column: str) -> list[dict]:
    positions = compute_positions(db, as_of=date.today(), currency=ccy)["positions"]
    if not positions:
        return []
    asset_ids = [position["asset_id"] for position in positions]
    changes = _change_map(db, asset_ids, column)
    sectors = _sector_map(db, asset_ids)

    # compute_positions returns one row per (account, asset) — a treemap wants one
    # tile per asset, so merge value/pnl across accounts before sizing
    merged: dict[int, dict] = {}
    for position in positions:
        value = position["value"] or 0.0
        if value <= 0:
            continue  # a treemap can't size a zero/negative tile
        tile = merged.get(position["asset_id"])
        if tile is not None:
            tile["size"] += value
            tile["pnl"] = (tile["pnl"] or 0.0) + (position["unrealized_pnl"] or 0.0)
            continue
        change = changes.get(position["asset_id"])
        merged[position["asset_id"]] = {
            "symbol": position["symbol"],
            "asset_id": position["asset_id"],
            "name": position["name"],
            "asset_class": position["asset_class"],
            "sector": sectors.get(position["asset_id"]),
            "size": value,
            "change_pct": round(change * 100, 4) if change is not None else 0.0,
            "price": position["last_price"],
            "weight_pct": None,
            "pnl": position["unrealized_pnl"],
        }
    return list(merged.values())


def _universe_tiles(db: Session, column: str, *, watchlist_only: bool) -> list[dict]:
    change_col = getattr(AssetMetrics, column)
    stmt = (
        select(
            Asset.id,
            Asset.symbol,
            Asset.name,
            Asset.asset_class,
            Asset.meta,
            change_col.label("change"),
            AssetMetrics.close,
            AssetMetrics.market_cap,
        )
        .outerjoin(AssetMetrics, AssetMetrics.asset_id == Asset.id)
        .where(Asset.is_active.is_(True))
    )
    if watchlist_only:
        stmt = stmt.where(Asset.id.in_(select(WatchlistItem.asset_id)))

    tiles = []
    for row in db.execute(stmt).all():
        market_cap = float(row.market_cap) if row.market_cap else None
        change = float(row.change) if row.change is not None else None
        sector = (row.meta or {}).get("profile", {}).get("sector")
        tiles.append(
            {
                "symbol": row.symbol,
                "asset_id": row.id,
                "name": row.name,
                "asset_class": row.asset_class,
                "sector": sector,
                # market-cap-weighted; fall back to a unit tile when unknown so the
                # asset still shows rather than vanishing.
                "size": market_cap if market_cap else 1.0,
                "change_pct": round(change * 100, 4) if change is not None else 0.0,
                "price": float(row.close) if row.close is not None else None,
                "weight_pct": None,
                "pnl": None,
            }
        )
    return tiles


def _change_map(db: Session, asset_ids: list[int], column: str) -> dict[int, float | None]:
    change_col = getattr(AssetMetrics, column)
    rows = db.execute(
        select(AssetMetrics.asset_id, change_col).where(AssetMetrics.asset_id.in_(asset_ids))
    ).all()
    return {asset_id: change for asset_id, change in rows}


def _sector_map(db: Session, asset_ids: list[int]) -> dict[int, str | None]:
    rows = db.execute(select(Asset.id, Asset.meta).where(Asset.id.in_(asset_ids))).all()
    return {
        asset_id: (meta or {}).get("profile", {}).get("sector") for asset_id, meta in rows
    }
