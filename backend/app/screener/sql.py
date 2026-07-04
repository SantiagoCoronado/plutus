"""Compile a filter AST to a SQLAlchemy WHERE clause over asset_metrics (live screening).

SQL three-valued logic applies: a NULL metric fails every comparison, and NOT(NULL)
is still NULL — so rows with missing metrics never match, even under "not".
pandas_eval.py reproduces exactly these semantics for backtests.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import and_, not_, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement

from app.models import Asset, AssetMetrics
from app.screener.ast import FieldRef, Group, Leaf, Node, Not, referenced_fields


@dataclass(frozen=True)
class ScreenHit:
    asset_id: int
    symbol: str
    name: str
    asset_class: str
    as_of: date | None
    values: dict[str, float | None]


def compile_where(node: Node) -> ColumnElement[bool]:
    if isinstance(node, Group):
        combine = and_ if node.kind == "all" else or_
        return combine(*(compile_where(child) for child in node.children))
    if isinstance(node, Not):
        return not_(compile_where(node.child))
    return _compile_leaf(node)


def _compile_leaf(leaf: Leaf) -> ColumnElement[bool]:
    col = getattr(AssetMetrics, leaf.field)
    value: Any = leaf.value
    if isinstance(value, FieldRef):
        value = getattr(AssetMetrics, value.field)

    match leaf.op:
        case ">":
            return col > value
        case "<":
            return col < value
        case ">=":
            return col >= value
        case "<=":
            return col <= value
        case "==":
            return col == value
        case "!=":
            return col != value
        case "between":
            return col.between(value[0], value[1])
        case "is_null":
            return col.is_(None)
        case "not_null":
            return col.is_not(None)
    raise ValueError(f"unhandled op {leaf.op!r}")  # unreachable after parse_ast


def run_screen(
    session: Session,
    node: Node,
    asset_class: str | None,
    limit: int = 200,
    asset_ids: Sequence[int] | None = None,
) -> list[ScreenHit]:
    fields = sorted(referenced_fields(node))
    stmt = (
        select(
            Asset.id,
            Asset.symbol,
            Asset.name,
            Asset.asset_class,
            AssetMetrics.as_of,
            AssetMetrics.close,
            *(getattr(AssetMetrics, field) for field in fields),
        )
        .join(AssetMetrics, AssetMetrics.asset_id == Asset.id)
        .where(Asset.is_active.is_(True))
        .where(compile_where(node))
        .order_by(Asset.symbol)
        .limit(limit)
    )
    if asset_class is not None:
        stmt = stmt.where(Asset.asset_class == asset_class)
    if asset_ids is not None:
        stmt = stmt.where(Asset.id.in_(asset_ids))

    hits = []
    for row in session.execute(stmt):
        values = {"close": row.close, **{field: getattr(row, field) for field in fields}}
        hits.append(
            ScreenHit(
                asset_id=row.id,
                symbol=row.symbol,
                name=row.name,
                asset_class=row.asset_class,
                as_of=row.as_of,
                values=values,
            )
        )
    return hits
