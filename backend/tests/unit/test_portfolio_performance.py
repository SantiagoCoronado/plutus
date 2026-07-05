"""TWR and XIRR against hand-computed fixtures."""

from datetime import date

import pandas as pd
import pytest

from app.portfolio.performance import annualize, twr, xirr


def series(values: dict[str, float]) -> pd.Series:
    return pd.Series(list(values.values()), index=pd.DatetimeIndex(list(values.keys())))


class TestTwr:
    def test_pure_growth_no_flows(self):
        values = series({"2026-01-01": 100.0, "2026-01-02": 110.0, "2026-01-03": 121.0})
        flows = series({"2026-01-01": 0.0, "2026-01-02": 0.0, "2026-01-03": 0.0})
        assert twr(values, flows) == pytest.approx(0.21)

    def test_deposit_into_flat_market_is_zero_return(self):
        # value doubles only because money arrived — performance is 0
        values = series({"2026-01-01": 100.0, "2026-01-02": 200.0, "2026-01-03": 200.0})
        flows = series({"2026-01-01": 0.0, "2026-01-02": 100.0, "2026-01-03": 0.0})
        assert twr(values, flows) == pytest.approx(0.0)

    def test_two_period_hand_fixture(self):
        # day2: (105 - 0 - 100)/100 = +5%; deposit 50 lands end of day2
        # day3: (170.5 - 0 - 155)/155 = +10%; chained: 1.05 * 1.10 - 1 = 15.5%
        values = series({"2026-01-01": 100.0, "2026-01-02": 155.0, "2026-01-03": 170.5})
        flows = series({"2026-01-02": 50.0})
        assert twr(values, flows) == pytest.approx(0.155)

    def test_withdrawal_flow_sign(self):
        # 100 -> 60 with a 50 withdrawal: (60 - (-50) - 100)/100 = +10%
        values = series({"2026-01-01": 100.0, "2026-01-02": 60.0})
        flows = series({"2026-01-02": -50.0})
        assert twr(values, flows) == pytest.approx(0.10)

    def test_skips_unfunded_leading_days(self):
        values = series(
            {"2026-01-01": 0.0, "2026-01-02": 0.0, "2026-01-03": 100.0, "2026-01-04": 110.0}
        )
        flows = series({"2026-01-03": 100.0})
        # first measurable period is day 4: (110-0-100)/100
        assert twr(values, flows) == pytest.approx(0.10)

    def test_empty_and_never_funded(self):
        empty = pd.Series(dtype=float)
        assert twr(empty, empty) is None
        values = series({"2026-01-01": 0.0, "2026-01-02": 0.0})
        assert twr(values, series({})) is None

    def test_annualize_gate(self):
        assert annualize(0.10, 200) is None
        assert annualize(0.21, 730) == pytest.approx(0.1, abs=1e-3)  # (1.21)^(1/2)-1


class TestXirr:
    def test_excel_verified_one_year_ten_percent(self):
        flows = [(date(2026, 1, 1), -1000.0), (date(2027, 1, 1), 1100.0)]
        assert xirr(flows) == pytest.approx(0.10, abs=1e-6)

    def test_two_contributions(self):
        # -1000 @ t0, -1000 @ +6mo, +2200 @ +1y — rate solves the NPV equation;
        # sanity: strictly between 0 and the naive 10%
        flows = [
            (date(2026, 1, 1), -1000.0),
            (date(2026, 7, 2), -1000.0),  # 182 days ≈ half a year
            (date(2027, 1, 1), 2200.0),
        ]
        rate = xirr(flows)
        assert rate is not None
        # verify the root actually zeroes the NPV
        npv = -1000 + -1000 * (1 + rate) ** (-182 / 365) + 2200 * (1 + rate) ** (-1.0)
        assert npv == pytest.approx(0.0, abs=1e-6)
        assert 0.10 < rate < 0.20

    def test_negative_return(self):
        flows = [(date(2026, 1, 1), -1000.0), (date(2027, 1, 1), 800.0)]
        assert xirr(flows) == pytest.approx(-0.20, abs=1e-6)

    def test_no_sign_change_returns_none(self):
        assert xirr([(date(2026, 1, 1), -100.0), (date(2026, 6, 1), -100.0)]) is None
        assert xirr([(date(2026, 1, 1), 100.0), (date(2026, 6, 1), 100.0)]) is None

    def test_fewer_than_two_flows(self):
        assert xirr([]) is None
        assert xirr([(date(2026, 1, 1), -100.0)]) is None
        # zero-amount flows are dropped before counting
        assert xirr([(date(2026, 1, 1), -100.0), (date(2026, 2, 1), 0.0)]) is None
