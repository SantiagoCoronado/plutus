"""Evaluate a filter AST against pandas Series (point-in-time backtests).

NULL-semantics contract (locked by tests/unit/test_screen_eval_equivalence.py):
inputs are cast to the nullable Float64 dtype, so comparisons yield <NA> where an
operand is missing, and &/|/~ on the nullable boolean dtype follow Kleene logic —
identical to SQL three-valued logic in sql.py. Callers apply .fillna(False) at the
end: an asset with a missing metric never passes, even under "not".
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd

from app.screener.ast import FieldRef, Group, Leaf, Node, Not


def evaluate_mask(node: Node, row: Mapping[str, pd.Series]) -> pd.Series:
    """row maps field name -> Series indexed by asset; returns a boolean-dtype mask.

    The result may contain <NA>; select with `evaluate_mask(...).fillna(False)`.
    """
    return _evaluate(node, row)


def _evaluate(node: Node, row: Mapping[str, pd.Series]) -> pd.Series:
    if isinstance(node, Group):
        masks = [_evaluate(child, row) for child in node.children]
        result = masks[0]
        for mask in masks[1:]:
            result = (result & mask) if node.kind == "all" else (result | mask)
        return result
    if isinstance(node, Not):
        return ~_evaluate(node.child, row)
    return _evaluate_leaf(node, row)


def _evaluate_leaf(leaf: Leaf, row: Mapping[str, pd.Series]) -> pd.Series:
    series = row[leaf.field].astype("Float64")
    value: Any = leaf.value
    if isinstance(value, FieldRef):
        value = row[value.field].astype("Float64")

    match leaf.op:
        case ">":
            return series > value
        case "<":
            return series < value
        case ">=":
            return series >= value
        case "<=":
            return series <= value
        case "==":
            return series == value
        case "!=":
            return series != value
        case "between":
            return (series >= value[0]) & (series <= value[1])
        case "is_null":
            return series.isna().astype("boolean")
        case "not_null":
            return series.notna().astype("boolean")
    raise ValueError(f"unhandled op {leaf.op!r}")  # unreachable after parse_ast
