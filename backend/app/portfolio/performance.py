"""Portfolio performance math: time-weighted return (TWR) and money-weighted
return (XIRR). Pure pandas/float — the valuation layer builds the inputs.

Flow classification: only money crossing the portfolio boundary is "external"
(deposits in, withdrawals out). Buys, sells, dividends, interest and fees move
value *within* the portfolio — they are performance, not flows. Transfers
between two tracked accounts net out at portfolio scope but are external at
single-account scope.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from scipy.optimize import brentq

# types that cross the boundary at total-portfolio scope (sign: + into, − out of)
EXTERNAL_FLOW_TYPES = {"deposit": 1.0, "withdrawal": -1.0}
# at single-account scope, transfers also cross the (account) boundary
ACCOUNT_FLOW_TYPES = {**EXTERNAL_FLOW_TYPES, "transfer_in": 1.0, "transfer_out": -1.0}


def twr(values: pd.Series, flows: pd.Series) -> float | None:
    """Time-weighted return over the whole window of `values`.

    values: daily portfolio value on a contiguous date grid.
    flows:  net external flow per day (+deposit / −withdrawal), assumed to land
            at END of day — so day t's return is judged before its flow:
            r_t = (V_t − F_t − V_{t−1}) / V_{t−1}.
    Days before the portfolio is funded (V ≤ 0) are skipped.
    """
    if values.empty:
        return None
    flows = flows.reindex(values.index, fill_value=0.0)
    growth = 1.0
    have_periods = False
    prev = None
    for day in values.index:
        value = float(values.loc[day])
        if prev is not None and prev > 0:
            growth *= (value - float(flows.loc[day]) - prev) / prev + 1.0
            have_periods = True
        prev = value
    return growth - 1.0 if have_periods else None


def annualize(twr_value: float, days: int) -> float | None:
    """Compound annual rate — only meaningful for windows of a year or more."""
    if days < 365:
        return None
    return (1.0 + twr_value) ** (365.0 / days) - 1.0


def xirr(cashflows: list[tuple[date, float]]) -> float | None:
    """Money-weighted annual return. Convention: contributions negative,
    withdrawals positive, and the terminal portfolio value as a final positive
    flow. Solved on Σ cf_i · (1+r)^(−Δt_i/365) = 0; None when the flows never
    change sign or a root can't be bracketed."""
    flows = [(day, amount) for day, amount in cashflows if amount != 0]
    if len(flows) < 2:
        return None
    if all(amount > 0 for _, amount in flows) or all(amount < 0 for _, amount in flows):
        return None

    t0 = min(day for day, _ in flows)
    years = [(day - t0).days / 365.0 for day, _ in flows]
    amounts = [amount for _, amount in flows]

    def net_present_value(rate: float) -> float:
        return sum(a * (1.0 + rate) ** (-t) for a, t in zip(amounts, years, strict=True))

    brackets = [-0.9999, -0.9, -0.5, -0.2, 0.0, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0]
    for low, high in zip(brackets, brackets[1:], strict=False):
        npv_low, npv_high = net_present_value(low), net_present_value(high)
        if npv_low == 0.0:
            return low
        if npv_low * npv_high < 0:
            return float(brentq(net_present_value, low, high))
    return None
