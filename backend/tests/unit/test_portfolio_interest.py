"""Bank-investment accrual math, hand-computed fixtures."""

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from app.models import BankInvestment, BankInvestmentTerm
from app.portfolio.interest import (
    Terms,
    accrued_interest,
    current_value,
    daily_value_series,
    effective_annual_rate,
    history_value_series,
    projected_maturity_value,
    year_fraction,
)
from app.portfolio.performance import twr
from app.portfolio.valuation import _term_history


def make_terms(**overrides) -> Terms:
    fields = {
        "kind": "fixed_term",
        "principal": 100_000.0,
        "annual_rate": 0.10,
        "rate_tiers": None,
        "cap_amount": None,
        "day_count": "act360",
        "compounding": "at_maturity",
        "start_date": date(2026, 1, 1),
        "maturity_date": date(2026, 7, 1),  # 181 days
    }
    fields.update(overrides)
    return Terms(**fields)


class TestSimpleAccrual:
    def test_act360_hand_math(self):
        # 100k at 10% for 90 days, ACT/360: 100000 * 0.10 * 90/360 = 2500
        terms = make_terms()
        assert accrued_interest(terms, date(2026, 4, 1)) == pytest.approx(2500.0)

    def test_act365_hand_math(self):
        # same 90 days on ACT/365: 100000 * 0.10 * 90/365 = 2465.7534...
        terms = make_terms(day_count="act365")
        assert accrued_interest(terms, date(2026, 4, 1)) == pytest.approx(100_000 * 0.10 * 90 / 365)

    def test_before_start_is_zero(self):
        assert accrued_interest(make_terms(), date(2025, 12, 15)) == 0.0

    def test_clamped_at_maturity(self):
        # 181-day term: asking a year later accrues exactly the term, no more
        terms = make_terms()
        at_maturity = accrued_interest(terms, date(2026, 7, 1))
        assert accrued_interest(terms, date(2027, 7, 1)) == pytest.approx(at_maturity)
        assert at_maturity == pytest.approx(100_000 * 0.10 * 181 / 360)

    def test_matured_value_stays_frozen(self):
        terms = make_terms()
        assert current_value(terms, date(2027, 1, 1)) == pytest.approx(
            projected_maturity_value(terms)
        )

    def test_demand_accrues_forever(self):
        terms = make_terms(kind="demand", maturity_date=None)
        one_year = accrued_interest(terms, date(2027, 1, 1))
        assert one_year == pytest.approx(100_000 * 0.10 * 365 / 360)

    def test_year_fraction(self):
        assert year_fraction(180, "act360") == pytest.approx(0.5)
        assert year_fraction(365, "act365") == pytest.approx(1.0)


class TestCompounding:
    def test_daily_compounding_hand_math(self):
        # 100k, 10%, 90 days daily on ACT/360: 100000*((1+0.1/360)^90 - 1)
        terms = make_terms(compounding="daily")
        expected = 100_000 * ((1 + 0.10 / 360) ** 90 - 1)
        assert accrued_interest(terms, date(2026, 4, 1)) == pytest.approx(expected)

    def test_monthly_compounding_with_stub(self):
        # Jan 1 -> Mar 16: two full months (31d, 28d) compound, 15-day stub simple
        terms = make_terms(compounding="monthly")
        balance = 100_000.0
        balance *= 1 + 0.10 * 31 / 360  # Jan
        balance *= 1 + 0.10 * 28 / 360  # Feb
        expected = (balance - 100_000) + balance * 0.10 * 15 / 360
        assert accrued_interest(terms, date(2026, 3, 16)) == pytest.approx(expected)

    def test_monthly_anniversary_clamps_at_month_end(self):
        # start Jan 31: the Feb anniversary is Feb 28 (2026 is not a leap year)
        terms = make_terms(
            compounding="monthly", start_date=date(2026, 1, 31), maturity_date=date(2026, 12, 31)
        )
        balance = 100_000.0 * (1 + 0.10 * 28 / 360)  # Jan 31 -> Feb 28
        expected = balance - 100_000.0
        assert accrued_interest(terms, date(2026, 2, 28)) == pytest.approx(expected)


class TestTiersAndCaps:
    # the spec's own example: 15% up to 25k, 5% above
    TIERS = [{"up_to": 25_000, "annual_rate": 0.15}, {"up_to": None, "annual_rate": 0.05}]

    def test_tiered_rates_split_the_balance(self):
        terms = make_terms(
            kind="demand", maturity_date=None, principal=40_000.0, rate_tiers=self.TIERS
        )
        # 360 days: 25k at 15% earns 3750; 15k at 5% earns 750
        got = accrued_interest(terms, date(2026, 12, 27))
        assert got == pytest.approx(25_000 * 0.15 + 15_000 * 0.05)

    def test_principal_below_first_tier(self):
        terms = make_terms(
            kind="demand", maturity_date=None, principal=10_000.0, rate_tiers=self.TIERS
        )
        assert accrued_interest(terms, date(2026, 12, 27)) == pytest.approx(10_000 * 0.15)

    def test_cap_amount_bounds_the_earning_balance(self):
        # 10% on up to 25k; the 15k excess earns nothing
        terms = make_terms(
            kind="demand", maturity_date=None, principal=40_000.0, cap_amount=25_000.0
        )
        assert accrued_interest(terms, date(2026, 12, 27)) == pytest.approx(25_000 * 0.10)

    def test_effective_annual_rate_blends_tiers(self):
        terms = make_terms(principal=40_000.0, rate_tiers=self.TIERS)
        # (25000*0.15 + 15000*0.05) / 40000 = 0.1125
        assert effective_annual_rate(terms) == pytest.approx(0.1125)

    def test_effective_annual_rate_flat(self):
        assert effective_annual_rate(make_terms()) == pytest.approx(0.10)


class TestDailySeries:
    def test_series_is_zero_before_start_and_monotone_after(self):
        terms = make_terms()
        series = daily_value_series(terms, date(2025, 12, 30), date(2026, 1, 10))
        assert series.iloc[0] == 0.0  # before start
        started = series[series.index >= "2026-01-01"]
        assert started.iloc[0] == pytest.approx(100_000.0)  # day 0: no accrual yet
        assert (started.diff().dropna() >= 0).all()
        assert started.iloc[-1] > 100_000.0


# the auto-renew fixture: 100k at 10% for 181 days capitalizes on Jul 1 and
# rolls into a second 181-day term at the same rate
CAPITALIZED = 100_000 * (1 + 0.10 * 181 / 360)


def renewal_history() -> list[Terms]:
    first = make_terms()
    second = make_terms(
        principal=CAPITALIZED,
        start_date=date(2026, 7, 1),
        maturity_date=date(2026, 12, 29),
    )
    return [first, second]


class TestHistorySeries:
    def test_continuous_and_monotone_across_renewal(self):
        series = history_value_series(renewal_history(), date(2026, 6, 20), date(2026, 7, 10))
        steps = series.diff().dropna()
        assert (steps >= 0).all()
        # no cliff: every daily step is accrual-sized, including the renewal day
        assert steps.max() < CAPITALIZED * 0.10 / 360 * 2
        assert series.loc["2026-07-01"] == pytest.approx(CAPITALIZED)

    def test_twr_across_rollover_equals_accrued_rate(self):
        # capitalization is return, not an external flow — TWR over a window
        # spanning the rollover is the accrued rate, not a principal cliff
        series = history_value_series(renewal_history(), date(2026, 6, 1), date(2026, 8, 1))
        flows = pd.Series(0.0, index=series.index)
        start_value = 100_000 * (1 + 0.10 * 151 / 360)  # Jun 1, day 151 of term 1
        end_value = CAPITALIZED * (1 + 0.10 * 31 / 360)  # Aug 1, day 31 of term 2
        assert twr(series, flows) == pytest.approx(end_value / start_value - 1)
        assert twr(series, flows) < 0.02  # two months of 10%/360, nowhere near 100%

    def test_single_term_history_matches_daily_value_series(self):
        terms = make_terms()
        expected = daily_value_series(terms, date(2025, 12, 30), date(2026, 1, 10))
        got = history_value_series([terms], date(2025, 12, 30), date(2026, 1, 10))
        pd.testing.assert_series_equal(got, expected)

    def test_zero_before_first_term(self):
        series = history_value_series(renewal_history(), date(2025, 12, 30), date(2026, 1, 2))
        assert series.iloc[0] == 0.0
        assert series.iloc[-1] > 0.0


class TestTermHistoryMapping:
    """valuation._term_history maps ORM rows -> Terms (transient, no session)."""

    def _investment(self) -> BankInvestment:
        return BankInvestment(
            account_id=1,
            name="pagare",
            kind="fixed_term",
            principal=Decimal("100000"),
            currency="MXN",
            annual_rate=Decimal("0.10"),
            rate_tiers=None,
            day_count="act360",
            compounding="at_maturity",
            start_date=date(2026, 1, 1),
            term_days=181,
            maturity_date=date(2026, 7, 1),
            cap_amount=None,
            auto_renew=True,
            status="active",
        )

    def test_legacy_investment_without_rows_is_the_parent_term(self):
        # pre-history data keeps working with no migration: exactly one term,
        # identical to what the parent row has always described
        assert _term_history(self._investment(), []) == [make_terms()]

    def test_rows_map_closed_and_open_terms(self):
        rows = [
            BankInvestmentTerm(
                investment_id=1,
                start_date=date(2025, 7, 4),
                end_date=date(2026, 1, 1),
                principal=Decimal("95000"),
                annual_rate=Decimal("0.10"),
                rate_tiers=None,
                cap_amount=None,
            ),
            BankInvestmentTerm(
                investment_id=1,
                start_date=date(2026, 1, 1),
                end_date=None,
                principal=Decimal("100000"),
                annual_rate=Decimal("0.10"),
                rate_tiers=None,
                cap_amount=None,
            ),
        ]
        closed, current = _term_history(self._investment(), rows)
        # the closed term freezes at its capitalization date
        assert closed.principal == 95_000.0
        assert closed.maturity_date == date(2026, 1, 1)
        # the open term follows the parent row's live maturity
        assert current == make_terms()
