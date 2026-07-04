"""Filter AST for the screener (spec §5.2).

Raw JSON shape:
    {"all": [<node>, ...]} | {"any": [<node>, ...]} | {"not": <node>}
    | {"field": "rsi_14", "op": "<", "value": 30}
    | {"field": "close", "op": ">", "value": {"field": "sma_200"}}   # column vs column

This module is pure: no SQLAlchemy or pandas imports. The two evaluators
(sql.py for live screening, pandas_eval.py for backtests) compile the same
parsed tree, and a unit test locks their NULL semantics together.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal

from app.models.asset_metrics import METRIC_COLUMNS

VALID_OPS = (">", "<", ">=", "<=", "==", "!=", "between", "is_null", "not_null")
# Ops valid only in strategy entry/exit conditions (series context), never in screens.
CROSS_OPS = ("crosses_above", "crosses_below")

SCREEN_FIELDS: frozenset[str] = frozenset(METRIC_COLUMNS)
# Fundamentals are stored as latest-annual snapshots only, so historical screens can't
# know them point-in-time; screen backtests must reject them to avoid look-ahead bias.
NON_PIT_FIELDS: frozenset[str] = frozenset(
    {
        "market_cap",
        "pe",
        "ps",
        "ev_ebitda",
        "gross_margin",
        "net_margin",
        "roe",
        "debt_to_equity",
        "revenue_growth_yoy",
    }
)
BACKTESTABLE_FIELDS: frozenset[str] = SCREEN_FIELDS - NON_PIT_FIELDS

MAX_DEPTH = 5
MAX_LEAVES = 30


@dataclass(frozen=True)
class FieldRef:
    field: str


@dataclass(frozen=True)
class Leaf:
    field: str
    op: str
    value: float | list[float] | FieldRef | None


@dataclass(frozen=True)
class Group:
    kind: Literal["all", "any"]
    children: tuple[Node, ...]


@dataclass(frozen=True)
class Not:
    child: Node


Node = Leaf | Group | Not


class AstError(ValueError):
    """Carries every validation failure with its JSON path, for a 422 detail payload."""

    def __init__(self, errors: list[dict[str, Any]]):
        self.errors = errors
        super().__init__(f"{len(errors)} filter AST error(s)")


def parse_ast(
    raw: Any,
    *,
    allowed_fields: frozenset[str] = SCREEN_FIELDS,
    extra_ops: tuple[str, ...] = (),
) -> Node:
    """Parse and validate a raw JSON filter AST; raises AstError with all failures."""
    errors: list[dict[str, Any]] = []
    leaf_count = 0

    def fail(path: str, error: str, **extra: Any) -> None:
        errors.append({"path": path, "error": error, **extra})

    def parse_node(raw_node: Any, path: str, depth: int) -> Node | None:
        nonlocal leaf_count
        if depth > MAX_DEPTH:
            fail(path, f"max nesting depth {MAX_DEPTH} exceeded")
            return None
        if not isinstance(raw_node, dict):
            fail(path, "node must be an object")
            return None

        keys = set(raw_node) & {"all", "any", "not", "field"}
        if len(keys) != 1:
            fail(path, 'node must have exactly one of: "all", "any", "not", "field"')
            return None
        key = keys.pop()

        if key in ("all", "any"):
            children_raw = raw_node[key]
            if not isinstance(children_raw, list) or not children_raw:
                fail(path, f'"{key}" must be a non-empty list')
                return None
            children = [
                parse_node(child, f"{path}.{key}[{i}]", depth + 1)
                for i, child in enumerate(children_raw)
            ]
            if any(child is None for child in children):
                return None
            return Group(kind=key, children=tuple(children))  # type: ignore[arg-type]

        if key == "not":
            child = parse_node(raw_node["not"], f"{path}.not", depth + 1)
            return Not(child=child) if child is not None else None

        return parse_leaf(raw_node, path)

    def parse_leaf(raw_leaf: dict, path: str) -> Leaf | None:
        nonlocal leaf_count
        leaf_count += 1
        ok = True

        field = raw_leaf.get("field")
        if field not in allowed_fields:
            fail(path, f"unknown field {field!r}", valid_fields=sorted(allowed_fields))
            ok = False

        op = raw_leaf.get("op")
        valid_ops = VALID_OPS + extra_ops
        if op not in valid_ops:
            fail(path, f"unknown op {op!r}", valid_ops=list(valid_ops))
            return None

        value = raw_leaf.get("value")
        if op in ("is_null", "not_null"):
            if value is not None:
                fail(path, f'"{op}" takes no value')
                ok = False
            value = None
        elif op == "between":
            if (
                not isinstance(value, list)
                or len(value) != 2
                or not all(_is_number(bound) for bound in value)
            ):
                fail(path, '"between" requires value [low, high] with two numbers')
                ok = False
            elif value[0] > value[1]:
                fail(path, '"between" bounds must satisfy low <= high')
                ok = False
        elif isinstance(value, dict):
            ref = value.get("field")
            if set(value) != {"field"} or ref not in allowed_fields:
                fail(
                    path,
                    f"value reference must be {{\"field\": <name>}} with a known field, "
                    f"got {value!r}",
                    valid_fields=sorted(allowed_fields),
                )
                ok = False
            else:
                value = FieldRef(field=ref)
        elif not _is_number(value):
            fail(path, f'op "{op}" requires a finite number or {{"field": ...}} value')
            ok = False

        if not ok:
            return None
        return Leaf(field=field, op=op, value=value)

    node = parse_node(raw, "$", 1)
    if leaf_count > MAX_LEAVES:
        fail("$", f"too many conditions: {leaf_count} > {MAX_LEAVES}")
    if errors:
        raise AstError(errors)
    assert node is not None  # errors is empty ⇒ every parse succeeded
    return node


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def iter_leaves(node: Node) -> Iterator[Leaf]:
    if isinstance(node, Leaf):
        yield node
    elif isinstance(node, Not):
        yield from iter_leaves(node.child)
    else:
        for child in node.children:
            yield from iter_leaves(child)


def referenced_fields(node: Node) -> set[str]:
    """Every metric column the AST touches, including {"field": ...} right-hand sides."""
    fields: set[str] = set()
    for leaf in iter_leaves(node):
        fields.add(leaf.field)
        if isinstance(leaf.value, FieldRef):
            fields.add(leaf.value.field)
    return fields
