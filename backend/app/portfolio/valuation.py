"""Portfolio valuation: cash ledgers, marked-to-market positions, value series,
performance and allocation reports.

Everything derives from the transaction ledger on read — there is no stored
position state to drift. All conversion goes through app/portfolio/fx.py; a
missing rate degrades to a warning on the affected slice, never an error.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Account, Asset, BankInvestment, BankInvestmentTerm, Ohlcv, Transaction
from app.portfolio import fx as fx_mod
from app.portfolio.interest import Terms, current_value, history_value_series
from app.portfolio.lots import TxnRow, average_cost, build_lots
from app.portfolio.performance import (
    ACCOUNT_FLOW_TYPES,
    EXTERNAL_FLOW_TYPES,
    annualize,
    twr,
    xirr,
)

# cash-leg effect of each transaction type, in transaction currency:
#   deposit +q · withdrawal −q · buy −(q·price+fees) · sell +(q·price−fees)
#   dividend/interest +q−fees · fee −q
# transfers move the *asset*, not tracked cash (external wallets have no ledger)


def cash_effect(txn: TxnRow) -> float:
    price = txn.price if txn.price is not None else 0.0
    if txn.type == "deposit":
        return txn.quantity
    if txn.type == "withdrawal":
        return -txn.quantity
    if txn.type == "buy":
        return -(txn.quantity * price + txn.fees)
    if txn.type == "sell":
        return txn.quantity * price - txn.fees
    if txn.type in ("dividend", "interest"):
        return txn.quantity - txn.fees
    if txn.type == "fee":
        return -txn.quantity
    return 0.0  # transfer_in / transfer_out


def cash_balances(transactions: Iterable[TxnRow]) -> dict[tuple[int, str], float]:
    """Running cash per (account_id, currency)."""
    balances: dict[tuple[int, str], float] = {}
    for txn in transactions:
        effect = cash_effect(txn)
        if effect != 0.0:
            key = (txn.account_id, txn.currency)
            balances[key] = balances.get(key, 0.0) + effect
    return balances


def to_txn_rows(transactions: Sequence) -> list[TxnRow]:
    """Project ORM Transaction rows (Numeric columns) into float TxnRows."""
    return [
        TxnRow(
            id=t.id,
            account_id=t.account_id,
            asset_id=t.asset_id,
            type=t.type,
            ts=t.ts,
            quantity=float(t.quantity),
            price=float(t.price) if t.price is not None else None,
            fees=float(t.fees),
            currency=t.currency,
            lot_links=t.lot_links,
        )
        for t in transactions
    ]


# --------------------------------------------------------------------------- #
# positions (point in time)                                                    #
# --------------------------------------------------------------------------- #


def compute_positions(
    session: Session, *, as_of: date, currency: str, account_id: int | None = None
) -> dict:
    txns = _load_txns(session, account_id)
    state = build_lots(txns)
    warnings: list[dict] = list(state.warnings)

    accounts = {a.id: a for a in session.scalars(select(Account)).all()}
    asset_ids = sorted({asset_id for _, asset_id in state.open_lots})
    assets = {
        a.id: a
        for a in session.scalars(select(Asset).where(Asset.id.in_(asset_ids or [0]))).all()
    }
    closes = _latest_closes(session, asset_ids, as_of)
    rates = _RateCache(session, as_of, warnings)

    realized_by_key: dict[tuple[int, int], float] = {}
    realized_total = 0.0
    for sale in state.realized:
        # realized P&L converts at the SALE-DATE rate — historical gains must not
        # drift with today's fx (unrealized figures still use the as_of rate)
        converted = sale.realized_pnl * rates.get(
            sale.currency, currency, on=min(sale.ts.date(), as_of)
        )
        realized_by_key[(sale.account_id, sale.asset_id)] = (
            realized_by_key.get((sale.account_id, sale.asset_id), 0.0) + converted
        )
        realized_total += converted

    positions = []
    for (acct_id, asset_id), lots in sorted(state.open_lots.items()):
        quantity = sum(lot.remaining for lot in lots)
        if quantity <= 0:
            continue
        asset = assets.get(asset_id)
        account = accounts.get(acct_id)
        close = closes.get(asset_id)
        native_ccy = asset.currency if asset else "USD"
        market_value_native = quantity * close if close is not None else None
        value = (
            market_value_native * rates.get(native_ccy, currency)
            if market_value_native is not None
            else None
        )
        cost_basis = sum(
            lot.remaining * lot.cost_per_unit * rates.get(lot.currency, currency) for lot in lots
        )
        if close is None and asset is not None:
            warnings.append(
                {"asset_id": asset_id, "warning": f"no price on record for {asset.symbol}"}
            )
        positions.append(
            {
                "account_id": acct_id,
                "account_name": account.name if account else None,
                "asset_id": asset_id,
                "symbol": asset.symbol if asset else str(asset_id),
                "name": asset.name if asset else None,
                "asset_class": asset.asset_class if asset else None,
                "quantity": quantity,
                "average_cost_native": average_cost(lots),
                # lots bought in MXN against a USD-quoted asset keep MXN costs
                "cost_currency": lots[0].currency,
                "native_currency": native_ccy,
                "last_price": close,
                "market_value_native": _round(market_value_native),
                "value": _round(value),
                "cost_basis": _round(cost_basis),
                "unrealized_pnl": _round(value - cost_basis) if value is not None else None,
                "unrealized_pnl_pct": (
                    round((value - cost_basis) / cost_basis, 6)
                    if value is not None and cost_basis > 0
                    else None
                ),
                "realized_pnl": _round(realized_by_key.get((acct_id, asset_id), 0.0)),
            }
        )

    cash = []
    for (acct_id, cash_ccy), amount in sorted(cash_balances(txns).items()):
        account = accounts.get(acct_id)
        cash.append(
            {
                "account_id": acct_id,
                "account_name": account.name if account else None,
                "currency": cash_ccy,
                "amount": _round(amount),
                "value": _round(amount * rates.get(cash_ccy, currency)),
            }
        )

    bank = []
    for investment in _load_investments(session, account_id):
        terms = _terms(investment)
        native_value = current_value(terms, as_of)
        account = accounts.get(investment.account_id)
        bank.append(
            {
                "id": investment.id,
                "account_id": investment.account_id,
                "account_name": account.name if account else None,
                "name": investment.name,
                "kind": investment.kind,
                "currency": investment.currency,
                "principal": float(investment.principal),
                "accrued_interest": _round(native_value - float(investment.principal)),
                "value_native": _round(native_value),
                "value": _round(native_value * rates.get(investment.currency, currency)),
                "maturity_date": investment.maturity_date,
                "status": investment.status,
            }
        )

    position_value = sum(p["value"] or 0.0 for p in positions)
    cash_value = sum(c["value"] or 0.0 for c in cash)
    bank_value = sum(b["value"] or 0.0 for b in bank)
    total = position_value + cash_value + bank_value
    for position in positions:
        position["weight"] = round((position["value"] or 0.0) / total, 6) if total > 0 else None

    cost_total = sum(p["cost_basis"] or 0.0 for p in positions)
    unrealized = sum(p["unrealized_pnl"] or 0.0 for p in positions)
    return {
        "as_of": as_of,
        "currency": currency,
        "totals": {
            "value": _round(total),
            "positions_value": _round(position_value),
            "cash_value": _round(cash_value),
            "bank_value": _round(bank_value),
            "cost_basis": _round(cost_total),
            "unrealized_pnl": _round(unrealized),
            "unrealized_pnl_pct": round(unrealized / cost_total, 6) if cost_total > 0 else None,
            "realized_pnl": _round(realized_total),
        },
        "positions": positions,
        "cash": cash,
        "bank_investments": bank,
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- #
# daily value series + performance                                             #
# --------------------------------------------------------------------------- #


def portfolio_value_series(
    session: Session, *, start: date, end: date, currency: str, account_id: int | None = None
) -> pd.DataFrame:
    """Daily DataFrame with columns value (portfolio, reporting currency) and
    flow (net external flow that day)."""
    grid = pd.date_range(start, end, freq="D")
    txns = _load_txns(session, account_id)
    value = pd.Series(0.0, index=grid)

    # marked-to-market holdings
    holdings = _holdings_by_asset(txns)
    if holdings:
        assets = {
            a.id: a
            for a in session.scalars(
                select(Asset).where(Asset.id.in_(list(holdings)))
            ).all()
        }
        fx_cache: dict[str, pd.Series] = {}
        for asset_id, quantity_steps in holdings.items():
            closes = _close_grid(session, asset_id, grid)
            if closes is None:
                continue
            quantity = _steps_to_grid(quantity_steps, grid)
            native_ccy = assets[asset_id].currency if asset_id in assets else "USD"
            if native_ccy not in fx_cache:
                fx_cache[native_ccy] = fx_mod.fx_series(session, native_ccy, currency, start, end)
            value = value.add((quantity * closes * fx_cache[native_ccy]).fillna(0.0))

    # cash, converted daily
    for (_, cash_ccy), steps in _cash_by_currency(txns, account_id).items():
        amounts = _steps_to_grid(steps, grid)
        rates = fx_mod.fx_series(session, cash_ccy, currency, start, end)
        value = value.add((amounts * rates).fillna(0.0))

    # bank investments accrue daily; walking the term history keeps the series
    # continuous across auto-renewals (capitalized interest is return, not flow)
    investments = _load_investments(session, account_id)
    term_rows = _load_term_rows(session, [investment.id for investment in investments])
    for investment in investments:
        history = _term_history(investment, term_rows.get(investment.id, []))
        series = history_value_series(history, start, end)
        rates = fx_mod.fx_series(session, investment.currency, currency, start, end)
        value = value.add((series * rates).fillna(0.0))

    # external flows (converted at that day's rate)
    flow_types = EXTERNAL_FLOW_TYPES if account_id is None else ACCOUNT_FLOW_TYPES
    flows = pd.Series(0.0, index=grid)
    fx_flow_cache: dict[str, pd.Series] = {}
    transfer_closes: dict[int, pd.Series | None] = {}
    transfer_ccy: dict[int, str] = {}

    def flow_rate(ccy: str, day: pd.Timestamp) -> float:
        if ccy not in fx_flow_cache:
            fx_flow_cache[ccy] = fx_mod.fx_series(session, ccy, currency, start, end)
        rate = fx_flow_cache[ccy].get(day)
        return 1.0 if rate is None or rate != rate else rate

    for txn in txns:
        sign = flow_types.get(txn.type)
        if sign is None:
            continue
        day = pd.Timestamp(txn.ts.date())
        if day < grid[0] or day > grid[-1]:
            continue
        if txn.type in ("deposit", "withdrawal"):
            flows.loc[day] += sign * txn.quantity * flow_rate(txn.currency, day)
            continue
        # transfers: carried cost when the row has one; exchange-synced crypto
        # transfers carry price=None and are marked to market on the flow day —
        # a zero-valued flow would book the moved asset as a fake gain/loss
        if txn.price is not None:
            flows.loc[day] += sign * txn.quantity * txn.price * flow_rate(txn.currency, day)
            continue
        if txn.asset_id is None:
            continue
        if txn.asset_id not in transfer_closes:
            transfer_closes[txn.asset_id] = _close_grid(session, txn.asset_id, grid)
            asset = session.get(Asset, txn.asset_id)
            transfer_ccy[txn.asset_id] = asset.currency if asset else "USD"
        closes = transfer_closes[txn.asset_id]
        close = closes.get(day) if closes is not None else None
        if close is None or close != close:
            continue
        flows.loc[day] += (
            sign * txn.quantity * float(close) * flow_rate(transfer_ccy[txn.asset_id], day)
        )

    return pd.DataFrame({"value": value, "flow": flows})


def performance_report(
    session: Session,
    *,
    period: str,
    currency: str,
    account_id: int | None = None,
    benchmark_symbol: str | None = None,
) -> dict:
    today = date.today()
    start = _period_start(session, period, today, account_id)
    frame = portfolio_value_series(
        session, start=start, end=today, currency=currency, account_id=account_id
    )
    values, flows = frame["value"], frame["flow"]

    twr_value = twr(values, flows)
    twr_annualized = annualize(twr_value, (today - start).days) if twr_value is not None else None

    cashflows = [
        (day.date(), -float(amount)) for day, amount in flows.items() if float(amount) != 0.0
    ]
    funded = values[values > 0]
    irr = None
    if not funded.empty:
        first_day = funded.index[0]
        # seed with the value already in place at window start (a contribution)
        opening = float(funded.iloc[0]) - float(flows.loc[first_day])
        if opening > 0:
            cashflows.append((first_day.date(), -opening))
        cashflows.append((funded.index[-1].date(), float(funded.iloc[-1])))
        irr = xirr(sorted(cashflows))

    indexed = _index_to_100(funded)
    benchmark = None
    symbol = benchmark_symbol or get_settings().benchmark_stock
    benchmark_asset = session.scalar(select(Asset).where(Asset.symbol == symbol))
    if benchmark_asset is not None and not funded.empty:
        closes = _close_grid(session, benchmark_asset.id, funded.index)
        if closes is not None:
            benchmark = {"symbol": symbol, "indexed": _points(_index_to_100(closes.dropna()))}

    return {
        "currency": currency,
        "period": period,
        "start": start,
        "end": today,
        "twr": round(twr_value, 6) if twr_value is not None else None,
        "twr_annualized": round(twr_annualized, 6) if twr_annualized is not None else None,
        "irr": round(irr, 6) if irr is not None else None,
        "series": _points(values),
        "indexed": _points(indexed),
        "benchmark": benchmark,
        "flows": [
            [day.date().isoformat(), _round(float(amount))]
            for day, amount in flows.items()
            if float(amount) != 0.0
        ],
    }


def allocation(session: Session, *, as_of: date, currency: str, by: str) -> dict:
    report = compute_positions(session, as_of=as_of, currency=currency)
    groups: dict[str, float] = {}

    def add(key: str, value: float | None) -> None:
        if value:
            groups[key] = groups.get(key, 0.0) + value

    if by == "asset_class":
        for position in report["positions"]:
            add(position["asset_class"] or "other", position["value"])
        for slice_ in report["cash"] + report["bank_investments"]:
            add("cash & fixed income", slice_["value"])
    elif by == "currency":
        for position in report["positions"]:
            add(position["native_currency"], position["value"])
        for cash in report["cash"]:
            add(cash["currency"], cash["value"])
        for investment in report["bank_investments"]:
            add(investment["currency"], investment["value"])
    else:  # account
        for position in report["positions"]:
            add(position["account_name"] or "?", position["value"])
        for slice_ in report["cash"] + report["bank_investments"]:
            add(slice_["account_name"] or "?", slice_["value"])

    total = sum(groups.values())
    return {
        "as_of": as_of,
        "currency": currency,
        "by": by,
        "total": _round(total),
        "groups": [
            {
                "key": key,
                "value": _round(value),
                "weight": round(value / total, 6) if total > 0 else None,
            }
            for key, value in sorted(groups.items(), key=lambda kv: -kv[1])
        ],
    }


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #


class _RateCache:
    """Point-in-time fx lookups. A missing pair warns and falls back to 1; a rate
    older than FX_MAX_STALE_DAYS still converts but carries a stale warning —
    neither case is ever a silent 1.0 blend. Warnings are machine-readable
    ({code, from_currency, to_currency, warning}) and deduped per pair."""

    def __init__(self, session: Session, as_of: date, warnings: list[dict]):
        self._session = session
        self._as_of = as_of
        self._warnings = warnings
        self._max_stale_days = get_settings().fx_max_stale_days
        self._cache: dict[tuple[str, str, date], float | None] = {}
        self._warned: set[tuple[str, str, str]] = set()

    def get(self, from_ccy: str, to_ccy: str, on: date | None = None) -> float:
        as_of = on or self._as_of
        key = (from_ccy, to_ccy, as_of)
        if key not in self._cache:
            rate, rate_date = fx_mod.fx_rate_with_age(self._session, from_ccy, to_ccy, as_of)
            if from_ccy != to_ccy:
                if rate is None:
                    self._warn(
                        "fx_missing", from_ccy, to_ccy,
                        f"no {from_ccy}->{to_ccy} rate on record; "
                        f"{from_ccy} amounts are unconverted",
                    )
                elif rate_date is not None and (as_of - rate_date).days > self._max_stale_days:
                    self._warn(
                        "fx_stale", from_ccy, to_ccy,
                        f"latest {from_ccy}->{to_ccy} rate is from {rate_date.isoformat()} "
                        f"({(as_of - rate_date).days} days before {as_of.isoformat()})",
                    )
            self._cache[key] = rate
        rate = self._cache[key]
        return rate if rate is not None else 1.0

    def _warn(self, code: str, from_ccy: str, to_ccy: str, message: str) -> None:
        dedup = (code, from_ccy, to_ccy)
        if dedup in self._warned:
            return
        self._warned.add(dedup)
        self._warnings.append(
            {
                "code": code,
                "from_currency": from_ccy,
                "to_currency": to_ccy,
                "warning": message,
            }
        )


def _load_txns(session: Session, account_id: int | None) -> list[TxnRow]:
    query = select(Transaction)
    if account_id is not None:
        query = query.where(Transaction.account_id == account_id)
    return to_txn_rows(session.scalars(query).all())


def _load_investments(session: Session, account_id: int | None) -> list[BankInvestment]:
    query = select(BankInvestment).where(BankInvestment.status != "closed")
    if account_id is not None:
        query = query.where(BankInvestment.account_id == account_id)
    return list(session.scalars(query).all())


def _terms(investment: BankInvestment) -> Terms:
    return Terms(
        kind=investment.kind,
        principal=float(investment.principal),
        annual_rate=float(investment.annual_rate),
        rate_tiers=investment.rate_tiers,
        cap_amount=float(investment.cap_amount) if investment.cap_amount is not None else None,
        day_count=investment.day_count,
        compounding=investment.compounding,
        start_date=investment.start_date,
        maturity_date=investment.maturity_date,
    )


def _load_term_rows(
    session: Session, investment_ids: list[int]
) -> dict[int, list[BankInvestmentTerm]]:
    """Append-only term history per investment id, oldest first."""
    if not investment_ids:
        return {}
    rows: dict[int, list[BankInvestmentTerm]] = {}
    for row in session.scalars(
        select(BankInvestmentTerm)
        .where(BankInvestmentTerm.investment_id.in_(investment_ids))
        .order_by(BankInvestmentTerm.investment_id, BankInvestmentTerm.start_date)
    ).all():
        rows.setdefault(row.investment_id, []).append(row)
    return rows


def _term_history(
    investment: BankInvestment, term_rows: Sequence[BankInvestmentTerm]
) -> list[Terms]:
    """One Terms per historical term. An investment with no rows (never rolled
    over, or created before the history table existed) is exactly the single
    term its parent row describes — legacy data needs no migration."""
    if not term_rows:
        return [_terms(investment)]
    return [
        Terms(
            kind=investment.kind,
            principal=float(row.principal),
            annual_rate=float(row.annual_rate),
            rate_tiers=row.rate_tiers,
            cap_amount=float(row.cap_amount) if row.cap_amount is not None else None,
            day_count=investment.day_count,
            compounding=investment.compounding,
            start_date=row.start_date,
            # a closed term freezes at its capitalization date; the open term
            # follows the parent's live maturity clamp
            maturity_date=row.end_date if row.end_date is not None else investment.maturity_date,
        )
        for row in term_rows
    ]


def _latest_closes(session: Session, asset_ids: list[int], as_of: date) -> dict[int, float]:
    if not asset_ids:
        return {}
    cutoff = pd.Timestamp(as_of).tz_localize("UTC") + pd.Timedelta(days=1)
    rows = session.execute(
        select(Ohlcv.asset_id, Ohlcv.close)
        .where(Ohlcv.asset_id.in_(asset_ids), Ohlcv.interval == "1d", Ohlcv.ts < cutoff)
        .order_by(Ohlcv.asset_id, Ohlcv.ts.desc())
        .distinct(Ohlcv.asset_id)
    ).all()
    return {asset_id: close for asset_id, close in rows}


QUANTITY_SIGNS = {"buy": 1.0, "transfer_in": 1.0, "sell": -1.0, "transfer_out": -1.0}


def _holdings_by_asset(txns: list[TxnRow]) -> dict[int, list[tuple[date, float]]]:
    """Per asset: (day, quantity delta) steps, portfolio-wide."""
    steps: dict[int, list[tuple[date, float]]] = {}
    for txn in sorted(txns, key=lambda t: (t.ts, t.id)):
        sign = QUANTITY_SIGNS.get(txn.type)
        if sign is None or txn.asset_id is None:
            continue
        steps.setdefault(txn.asset_id, []).append((txn.ts.date(), sign * txn.quantity))
    return steps


def _cash_by_currency(
    txns: list[TxnRow], account_id: int | None
) -> dict[tuple[int | None, str], list[tuple[date, float]]]:
    steps: dict[tuple[int | None, str], list[tuple[date, float]]] = {}
    for txn in sorted(txns, key=lambda t: (t.ts, t.id)):
        effect = cash_effect(txn)
        if effect == 0.0:
            continue
        key = (None if account_id is None else txn.account_id, txn.currency)
        steps.setdefault(key, []).append((txn.ts.date(), effect))
    return steps


def _steps_to_grid(steps: list[tuple[date, float]], grid: pd.DatetimeIndex) -> pd.Series:
    """Cumulative step function sampled on the daily grid."""
    series = pd.Series(0.0, index=grid)
    for day, delta in steps:
        stamp = pd.Timestamp(day)
        if stamp <= grid[-1]:
            series.loc[max(stamp, grid[0]) :] += delta
    return series


def _close_grid(
    session: Session, asset_id: int, grid: pd.DatetimeIndex
) -> pd.Series | None:
    start = grid[0] - pd.Timedelta(days=14)
    rows = session.execute(
        select(Ohlcv.ts, Ohlcv.close)
        .where(
            Ohlcv.asset_id == asset_id,
            Ohlcv.interval == "1d",
            Ohlcv.ts >= start.tz_localize("UTC"),
            Ohlcv.ts < (grid[-1] + pd.Timedelta(days=1)).tz_localize("UTC"),
        )
        .order_by(Ohlcv.ts)
    ).all()
    if not rows:
        return None
    closes = pd.Series([r.close for r in rows], index=pd.DatetimeIndex([r.ts for r in rows]))
    return fx_mod.align_to_grid(closes, grid)


def _period_start(
    session: Session, period: str, today: date, account_id: int | None
) -> date:
    if period == "ytd":
        return date(today.year, 1, 1)
    fixed = {"1m": 30, "3m": 91, "6m": 182, "1y": 365}
    if period in fixed:
        return today - timedelta(days=fixed[period])
    # all: from the first recorded event
    query = select(Transaction.ts)
    if account_id is not None:
        query = query.where(Transaction.account_id == account_id)
    first_txn = session.scalar(query.order_by(Transaction.ts).limit(1))
    candidates = [first_txn.date() if first_txn else None]
    inv_query = select(BankInvestment.start_date).order_by(BankInvestment.start_date).limit(1)
    if account_id is not None:
        inv_query = inv_query.where(BankInvestment.account_id == account_id)
    candidates.append(session.scalar(inv_query))
    # a renewed investment's parent start_date moved forward; its inception
    # survives in the first term-history row
    term_query = (
        select(BankInvestmentTerm.start_date).order_by(BankInvestmentTerm.start_date).limit(1)
    )
    if account_id is not None:
        term_query = term_query.join(
            BankInvestment, BankInvestment.id == BankInvestmentTerm.investment_id
        ).where(BankInvestment.account_id == account_id)
    candidates.append(session.scalar(term_query))
    starts = [c for c in candidates if c is not None]
    return min(starts) if starts else today - timedelta(days=365)


def _index_to_100(series: pd.Series) -> pd.Series:
    positive = series[series > 0]
    if positive.empty:
        return pd.Series(dtype=float)
    return series / positive.iloc[0] * 100.0


def _points(series: pd.Series) -> list[list]:
    return [
        [day.date().isoformat(), _round(float(value))]
        for day, value in series.items()
        if value == value  # drop NaN
    ]


def _round(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None
