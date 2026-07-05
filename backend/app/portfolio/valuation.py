"""Portfolio valuation: cash ledgers, marked-to-market positions, value series.

Everything derives from the transaction ledger on read — there is no stored
position state to drift.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from app.portfolio.lots import TxnRow

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
