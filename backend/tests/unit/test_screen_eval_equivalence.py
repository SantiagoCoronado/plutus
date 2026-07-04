"""SQL ↔ pandas evaluator equivalence lock.

The live screener (sql.py) relies on SQL three-valued NULL logic; the backtest
evaluator (pandas_eval.py) reproduces it with nullable dtypes + Kleene logic.
This test runs the same ASTs over the same rows through BOTH engines (SQLite
implements standard SQL NULL semantics) and requires identical selections —
especially around NULLs under not/any/between.
"""

import pandas as pd
import pytest
import sqlalchemy as sa
from sqlalchemy import select

from app.models import AssetMetrics
from app.screener.ast import parse_ast
from app.screener.pandas_eval import evaluate_mask
from app.screener.sql import compile_where

FIELDS = ("close", "sma_200", "rsi_14", "pe", "volume_avg_20")

# asset_id -> metric values (None = SQL NULL / pandas NA)
ROWS = {
    1: {"close": 100.0, "sma_200": 90.0, "rsi_14": 25.0, "pe": 30.0, "volume_avg_20": 1e6},
    2: {"close": 80.0, "sma_200": 90.0, "rsi_14": 75.0, "pe": None, "volume_avg_20": 2e6},
    3: {"close": None, "sma_200": 90.0, "rsi_14": 50.0, "pe": 10.0, "volume_avg_20": None},
    4: {"close": 90.0, "sma_200": None, "rsi_14": None, "pe": 15.0, "volume_avg_20": 3e6},
    5: {"close": 90.0, "sma_200": 90.0, "rsi_14": 30.0, "pe": 20.0, "volume_avg_20": 4e6},
}

CASES = [
    {"field": "rsi_14", "op": "<", "value": 30},
    {"field": "rsi_14", "op": "<=", "value": 30},
    {"field": "rsi_14", "op": "==", "value": 30},
    {"field": "rsi_14", "op": "!=", "value": 30},
    {"field": "rsi_14", "op": "between", "value": [25, 50]},
    {"field": "pe", "op": "is_null"},
    {"field": "pe", "op": "not_null"},
    # NULL under not: NOT(rsi < 30) must still drop asset 4 (rsi NULL)
    {"not": {"field": "rsi_14", "op": "<", "value": 30}},
    {"not": {"field": "rsi_14", "op": "between", "value": [25, 50]}},
    # column vs column with NULLs on either side (assets 3 and 4)
    {"field": "close", "op": ">", "value": {"field": "sma_200"}},
    {"field": "close", "op": "==", "value": {"field": "sma_200"}},
    {"not": {"field": "close", "op": ">", "value": {"field": "sma_200"}}},
    # groups mixing NULL and non-NULL legs
    {"all": [
        {"field": "rsi_14", "op": "<", "value": 60},
        {"field": "pe", "op": "<", "value": 25},
    ]},
    {"any": [
        {"field": "pe", "op": "<", "value": 12},
        {"field": "rsi_14", "op": ">", "value": 70},
    ]},
    {"not": {"any": [
        {"field": "pe", "op": "<", "value": 12},
        {"field": "rsi_14", "op": ">", "value": 70},
    ]}},
    {"not": {"all": [
        {"field": "close", "op": "not_null"},
        {"field": "volume_avg_20", "op": ">", "value": 1.5e6},
    ]}},
    # spec-style compound
    {"all": [
        {"field": "close", "op": ">", "value": {"field": "sma_200"}},
        {"any": [
            {"field": "rsi_14", "op": "<", "value": 40},
            {"field": "pe", "op": "between", "value": [5, 18]},
        ]},
    ]},
]


@pytest.fixture(scope="module")
def sqlite_conn():
    engine = sa.create_engine("sqlite://")
    cols = ", ".join(f"{f} FLOAT" for f in FIELDS)
    with engine.connect() as conn:
        conn.execute(sa.text(f"CREATE TABLE asset_metrics (asset_id INTEGER, {cols})"))
        for asset_id, row in ROWS.items():
            conn.execute(
                sa.text(
                    f"INSERT INTO asset_metrics (asset_id, {', '.join(FIELDS)}) "
                    f"VALUES (:asset_id, {', '.join(':' + f for f in FIELDS)})"
                ),
                {"asset_id": asset_id, **row},
            )
        conn.commit()
        yield conn


@pytest.fixture(scope="module")
def panel_row():
    index = list(ROWS)
    return {
        field: pd.Series([ROWS[i][field] for i in index], index=index, dtype="Float64")
        for field in FIELDS
    }


@pytest.mark.parametrize("raw", CASES, ids=[str(i) for i in range(len(CASES))])
def test_sql_and_pandas_select_identical_assets(raw, sqlite_conn, panel_row):
    node = parse_ast(raw)

    stmt = select(AssetMetrics.asset_id).where(compile_where(node))
    sql_selected = {row.asset_id for row in sqlite_conn.execute(stmt)}

    mask = evaluate_mask(node, panel_row).fillna(False)
    pandas_selected = set(mask[mask].index)

    assert sql_selected == pandas_selected, (
        f"engines disagree for {raw!r}: sql={sorted(sql_selected)} "
        f"pandas={sorted(pandas_selected)}"
    )


def test_null_never_passes_even_under_not(panel_row):
    # asset 4 has rsi_14 NULL: it must fail both the condition and its negation
    for raw in ({"field": "rsi_14", "op": "<", "value": 30},
                {"not": {"field": "rsi_14", "op": "<", "value": 30}}):
        mask = evaluate_mask(parse_ast(raw), panel_row).fillna(False)
        assert not mask.loc[4]
