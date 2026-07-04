"""Strategy condition evaluation: cross operators, NaN warmup, threshold rules."""

from datetime import UTC

import numpy as np
import pandas as pd
import pytest

from app.backtest.strategy import (
    STRATEGY_FIELDS,
    build_signal_frame,
    parse_condition,
)
from app.screener.ast import AstError


def frame_from_close(close_values, volume=True):
    n = len(close_values)
    idx = pd.date_range("2025-01-01", periods=n, freq="D", tz=UTC)
    close = pd.Series(close_values, index=idx, dtype=float)
    return pd.DataFrame(
        {
            "open": close.shift(1).fillna(close.iloc[0]),
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": np.full(n, 1e6) if volume else np.full(n, np.nan),
        },
        index=idx,
    )


class TestParseCondition:
    def test_accepts_price_fields_and_cross_ops(self):
        node = parse_condition(
            {"field": "close", "op": "crosses_above", "value": {"field": "sma_20"}}
        )
        assert node.op == "crosses_above"

    def test_accepts_scalar_cross(self):
        assert parse_condition({"field": "rsi_14", "op": "crosses_below", "value": 30})

    def test_rejects_fundamentals(self):
        with pytest.raises(AstError):
            parse_condition({"field": "pe", "op": "<", "value": 20})
        assert "pe" not in STRATEGY_FIELDS

    def test_rejects_cross_with_bad_value(self):
        with pytest.raises(AstError):
            parse_condition({"field": "close", "op": "crosses_above", "value": "sma_20"})


class TestCrossSemantics:
    def test_cross_fires_only_on_crossing_bar(self):
        # close crosses above 105 exactly once, at index 3
        df = frame_from_close([100, 102, 104, 106, 108, 110])
        entry = parse_condition({"field": "close", "op": "crosses_above", "value": 105})
        exit_ = parse_condition({"field": "close", "op": "crosses_below", "value": 0})
        signals = build_signal_frame(df, entry, exit_)
        assert list(signals["entry_sig"]) == [0, 0, 0, 1, 0, 0]

    def test_cross_below_mirrors(self):
        df = frame_from_close([110, 108, 106, 104, 102, 100])
        entry = parse_condition({"field": "close", "op": "crosses_below", "value": 105})
        signals = build_signal_frame(df, entry, entry)
        assert list(signals["entry_sig"]) == [0, 0, 0, 1, 0, 0]

    def test_series_vs_series_cross(self):
        # close vs sma_20: rising series crosses its own lagging mean exactly once
        # after a dip; verify at most one fire and never during warmup
        values = list(np.linspace(120, 100, 25)) + list(np.linspace(100, 140, 25))
        df = frame_from_close(values)
        entry = parse_condition(
            {"field": "close", "op": "crosses_above", "value": {"field": "sma_20"}}
        )
        signals = build_signal_frame(df, entry, entry)["entry_sig"]
        assert signals.iloc[:20].sum() == 0  # sma_20 warmup: no valid cross possible
        assert signals.sum() == 1

    def test_first_bar_never_fires(self):
        df = frame_from_close([106, 108, 110])
        entry = parse_condition({"field": "close", "op": "crosses_above", "value": 105})
        signals = build_signal_frame(df, entry, entry)
        assert signals["entry_sig"].iloc[0] == 0  # no previous bar -> no cross


class TestThresholdAndGating:
    def test_threshold_rule_with_kleene_nan(self):
        df = frame_from_close([100, 102, 104, 106], volume=False)
        # obv needs volume -> all NA -> never fires, even negated
        entry = parse_condition({"not": {"field": "obv", "op": ">", "value": 0}})
        signals = build_signal_frame(df, entry, entry)
        assert signals["entry_sig"].sum() == 0

    def test_compound_condition(self):
        df = frame_from_close([100, 102, 104, 106, 108, 110])
        entry = parse_condition(
            {
                "all": [
                    {"field": "close", "op": ">", "value": 103},
                    {"field": "close", "op": "<", "value": 109},
                ]
            }
        )
        signals = build_signal_frame(df, entry, entry)
        assert list(signals["entry_sig"]) == [0, 0, 1, 1, 1, 0]
