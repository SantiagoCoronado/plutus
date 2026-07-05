"""Bank-investment interest math (spec §7.4): term deposits and interest-bearing
demand balances. Pure date arithmetic — bookkeeping, never a bank connection.

Rates are decimal fractions (0.105 = 10.5% a year). `rate_tiers`, when present,
wins over the flat `annual_rate` + `cap_amount` pair:
    [{"up_to": 25000, "annual_rate": 0.15}, {"up_to": null, "annual_rate": 0.05}]
means the first 25k earns 15% and everything above it 5%.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

import pandas as pd

DAY_COUNT_BASES = {"act360": 360, "act365": 365}


@dataclass(frozen=True)
class Terms:
    """The rate-relevant fields of a bank investment (floats, pure)."""

    kind: str  # demand | fixed_term
    principal: float
    annual_rate: float
    rate_tiers: list[dict] | None
    cap_amount: float | None
    day_count: str  # act360 | act365
    compounding: str  # daily | monthly | at_maturity
    start_date: date
    maturity_date: date | None  # None for demand


def year_fraction(days: int, day_count: str) -> float:
    return days / DAY_COUNT_BASES[day_count]


def accrued_interest(terms: Terms, as_of: date) -> float:
    """Interest earned from start_date to as_of (clamped to maturity)."""
    end = as_of
    if terms.maturity_date is not None:
        end = min(end, terms.maturity_date)
    days = max(0, (end - terms.start_date).days)
    if days == 0:
        return 0.0

    base = DAY_COUNT_BASES[terms.day_count]
    total = 0.0
    for principal, rate in _tier_slices(terms):
        if terms.compounding == "daily":
            total += principal * ((1 + rate / base) ** days - 1)
        elif terms.compounding == "monthly":
            total += _monthly_compound(principal, rate, base, terms.start_date, end)
        else:  # at_maturity: simple interest
            total += principal * rate * days / base
    return total


def current_value(terms: Terms, as_of: date) -> float:
    """Principal plus accrued interest. A matured fixed-term investment that is
    not yet closed stays frozen at its maturity value (the clamp does this)."""
    return terms.principal + accrued_interest(terms, as_of)


def projected_maturity_value(terms: Terms) -> float | None:
    if terms.maturity_date is None:
        return None
    return terms.principal + accrued_interest(terms, terms.maturity_date)


def effective_annual_rate(terms: Terms) -> float:
    """Blended first-year rate across tiers — what the whole balance earns."""
    if terms.principal <= 0:
        return 0.0
    earned = sum(p * r for p, r in _tier_slices(terms))
    return earned / terms.principal


def daily_value_series(terms: Terms, start: date, end: date) -> pd.Series:
    """Value of the investment on each calendar day in [start, end] — the input
    the portfolio valuation grid needs. Zero before the investment starts."""
    days = pd.date_range(start, end, freq="D")
    values = [
        0.0 if day.date() < terms.start_date else current_value(terms, day.date())
        for day in days
    ]
    return pd.Series(values, index=days)


def _tier_slices(terms: Terms) -> list[tuple[float, float]]:
    """[(slice_of_principal, annual_rate)] — the earning structure."""
    principal = terms.principal
    if terms.rate_tiers:
        slices: list[tuple[float, float]] = []
        floor = 0.0
        for tier in terms.rate_tiers:
            up_to = tier.get("up_to")
            ceiling = principal if up_to is None else min(float(up_to), principal)
            if ceiling > floor:
                slices.append((ceiling - floor, float(tier["annual_rate"])))
                floor = ceiling
        if floor < principal:  # above the last bounded tier: earns nothing
            slices.append((principal - floor, 0.0))
        return slices
    if terms.cap_amount is not None and principal > float(terms.cap_amount):
        cap = float(terms.cap_amount)
        return [(cap, terms.annual_rate), (principal - cap, 0.0)]
    return [(principal, terms.annual_rate)]


def _monthly_compound(principal: float, rate: float, base: int, start: date, end: date) -> float:
    """Interest credited on each monthly anniversary of start (actual-day
    periods); the trailing stub accrues simple interest on the running balance."""
    balance = principal
    period_start = start
    while True:
        anniversary = _add_months(period_start, 1)
        if anniversary > end:
            break
        days = (anniversary - period_start).days
        balance *= 1 + rate * days / base
        period_start = anniversary
    stub_days = (end - period_start).days
    if stub_days > 0:
        balance += balance * rate * stub_days / base
    return balance - principal


def _add_months(day: date, months: int) -> date:
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    # clamp to month end: Jan 31 + 1 month -> Feb 28/29
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))
