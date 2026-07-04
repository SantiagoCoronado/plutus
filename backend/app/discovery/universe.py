"""Resolve a mandate's universe_def to concrete asset ids.

Shapes (validated at the API layer; interpreted defensively here):
  {"type": "class"}                                  — every active asset of the class
  {"type": "watchlist", "watchlist_id": N}           — watchlist members, class-scoped
  {"type": "market_cap_floor", "min_market_cap": X}  — nightly snapshot market cap >= X
  {"type": "top_by_market_cap", "count": N}          — N largest by snapshot market cap
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Asset, AssetMetrics, Mandate, WatchlistItem

UNIVERSE_TYPES = ("class", "watchlist", "market_cap_floor", "top_by_market_cap")


def resolve_universe(session: Session, mandate: Mandate) -> list[int]:
    base = select(Asset.id).where(
        Asset.is_active.is_(True), Asset.asset_class == mandate.asset_class
    )
    definition = mandate.universe_def or {}
    kind = definition.get("type", "class")

    if kind == "class":
        stmt = base.order_by(Asset.symbol)
    elif kind == "watchlist":
        stmt = (
            base.join(WatchlistItem, WatchlistItem.asset_id == Asset.id)
            .where(WatchlistItem.watchlist_id == definition["watchlist_id"])
            .order_by(Asset.symbol)
        )
    elif kind == "market_cap_floor":
        stmt = (
            base.join(AssetMetrics, AssetMetrics.asset_id == Asset.id)
            .where(AssetMetrics.market_cap >= definition["min_market_cap"])
            .order_by(Asset.symbol)
        )
    elif kind == "top_by_market_cap":
        stmt = (
            base.join(AssetMetrics, AssetMetrics.asset_id == Asset.id)
            .order_by(AssetMetrics.market_cap.desc().nulls_last(), Asset.symbol)
            .limit(int(definition["count"]))
        )
    else:
        raise ValueError(f"unknown universe type {kind!r}")
    return list(session.scalars(stmt))
