"""Filter AST parsing/validation — a correctness-critical core (spec §12)."""

import pytest

from app.models.asset_metrics import METRIC_COLUMNS
from app.screener.ast import (
    BACKTESTABLE_FIELDS,
    MAX_LEAVES,
    NON_PIT_FIELDS,
    SCREEN_FIELDS,
    AstError,
    FieldRef,
    Group,
    Leaf,
    Not,
    iter_leaves,
    parse_ast,
    referenced_fields,
)

# The exact example from spec §5.2
SPEC_EXAMPLE = {
    "all": [
        {"field": "market_cap", "op": ">", "value": 2e9},
        {"field": "rsi_14", "op": "<", "value": 30},
        {"field": "revenue_growth_yoy", "op": ">", "value": 0.15},
    ]
}


def errors_of(raw, **kwargs) -> list[dict]:
    with pytest.raises(AstError) as excinfo:
        parse_ast(raw, **kwargs)
    return excinfo.value.errors


class TestParseHappyPaths:
    def test_spec_example(self):
        node = parse_ast(SPEC_EXAMPLE)
        assert isinstance(node, Group) and node.kind == "all"
        assert [leaf.field for leaf in node.children] == [
            "market_cap",
            "rsi_14",
            "revenue_growth_yoy",
        ]

    def test_single_leaf(self):
        node = parse_ast({"field": "rsi_14", "op": "<", "value": 30})
        assert node == Leaf(field="rsi_14", op="<", value=30)

    def test_nested_any_not(self):
        node = parse_ast(
            {
                "all": [
                    {"any": [
                        {"field": "rsi_14", "op": "<", "value": 30},
                        {"field": "percent_b", "op": "<", "value": 0},
                    ]},
                    {"not": {"field": "adx_14", "op": "<", "value": 20}},
                ]
            }
        )
        assert isinstance(node.children[0], Group) and node.children[0].kind == "any"
        assert isinstance(node.children[1], Not)

    def test_between(self):
        node = parse_ast({"field": "rsi_14", "op": "between", "value": [30, 70]})
        assert node.value == [30, 70]

    def test_null_ops(self):
        assert parse_ast({"field": "pe", "op": "is_null"}).op == "is_null"
        assert parse_ast({"field": "pe", "op": "not_null", "value": None}).op == "not_null"

    def test_field_ref_rhs(self):
        node = parse_ast({"field": "close", "op": ">", "value": {"field": "sma_200"}})
        assert node.value == FieldRef(field="sma_200")

    def test_iter_leaves_and_referenced_fields(self):
        node = parse_ast(
            {
                "all": [
                    {"field": "close", "op": ">", "value": {"field": "sma_200"}},
                    {"not": {"field": "rsi_14", "op": ">", "value": 70}},
                ]
            }
        )
        assert len(list(iter_leaves(node))) == 2
        assert referenced_fields(node) == {"close", "sma_200", "rsi_14"}


class TestValidationErrors:
    def test_unknown_field_lists_valid_fields(self):
        errors = errors_of({"field": "bogus", "op": ">", "value": 1})
        assert errors[0]["path"] == "$"
        assert "bogus" in errors[0]["error"]
        assert errors[0]["valid_fields"] == sorted(SCREEN_FIELDS)

    def test_unknown_op(self):
        errors = errors_of({"field": "rsi_14", "op": "~=", "value": 1})
        assert "~=" in errors[0]["error"]
        assert ">" in errors[0]["valid_ops"]

    def test_cross_ops_rejected_in_screens(self):
        errors = errors_of({"field": "close", "op": "crosses_above", "value": {"field": "sma_50"}})
        assert "crosses_above" in errors[0]["error"]

    def test_between_arity(self):
        errors = errors_of({"field": "rsi_14", "op": "between", "value": [1]})
        assert "between" in errors[0]["error"]

    def test_between_order(self):
        errors = errors_of({"field": "rsi_14", "op": "between", "value": [70, 30]})
        assert "low <= high" in errors[0]["error"]

    def test_null_op_with_value(self):
        errors = errors_of({"field": "pe", "op": "is_null", "value": 3})
        assert "takes no value" in errors[0]["error"]

    def test_non_numeric_value(self):
        errors = errors_of({"field": "rsi_14", "op": "<", "value": "thirty"})
        assert "finite number" in errors[0]["error"]

    def test_nan_and_bool_values_rejected(self):
        assert errors_of({"field": "rsi_14", "op": "<", "value": float("nan")})
        assert errors_of({"field": "rsi_14", "op": "<", "value": True})

    def test_bad_field_ref(self):
        errors = errors_of({"field": "close", "op": ">", "value": {"field": "bogus"}})
        assert "value reference" in errors[0]["error"]

    def test_empty_group(self):
        errors = errors_of({"all": []})
        assert "non-empty" in errors[0]["error"]

    def test_node_shape(self):
        assert errors_of([1, 2])[0]["error"] == "node must be an object"
        assert "exactly one" in errors_of({"field": "rsi_14", "all": []})[0]["error"]
        assert "exactly one" in errors_of({})[0]["error"]

    def test_max_depth(self):
        raw = {"field": "rsi_14", "op": "<", "value": 30}
        for _ in range(6):
            raw = {"not": raw}
        errors = errors_of(raw)
        assert any("depth" in e["error"] for e in errors)

    def test_max_leaves(self):
        raw = {"all": [{"field": "rsi_14", "op": "<", "value": i} for i in range(MAX_LEAVES + 1)]}
        errors = errors_of(raw)
        assert any("too many conditions" in e["error"] for e in errors)

    def test_errors_accumulate_with_paths(self):
        errors = errors_of(
            {
                "all": [
                    {"field": "bogus1", "op": ">", "value": 1},
                    {"any": [{"field": "rsi_14", "op": "bad_op", "value": 1}]},
                ]
            }
        )
        paths = [e["path"] for e in errors]
        assert "$.all[0]" in paths and "$.all[1].any[0]" in paths


class TestFieldSets:
    def test_screen_fields_are_metric_columns(self):
        assert SCREEN_FIELDS == frozenset(METRIC_COLUMNS)

    def test_non_pit_fields_are_exactly_the_fundamentals(self):
        assert NON_PIT_FIELDS == {
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
        assert NON_PIT_FIELDS < SCREEN_FIELDS

    def test_backtest_rejects_non_pit_fields(self):
        for field in sorted(NON_PIT_FIELDS):
            errors = errors_of(
                {"field": field, "op": ">", "value": 1},
                allowed_fields=BACKTESTABLE_FIELDS,
            )
            assert errors[0]["valid_fields"] == sorted(BACKTESTABLE_FIELDS)

    def test_backtest_allows_technical_fields(self):
        node = parse_ast(
            {"field": "rsi_14", "op": "<", "value": 30},
            allowed_fields=BACKTESTABLE_FIELDS,
        )
        assert isinstance(node, Leaf)
