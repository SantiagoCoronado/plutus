"""FIFO / specific-ID lot engine — the P&L core.

Lots are *derived* from the transaction ledger on every read; nothing here is
persisted, so edits to transactions can never leave stale lot state behind.
All amounts are floats in the transaction's native currency; currency
conversion happens later, in the valuation layer.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime


class LotError(Exception):
    """Raised in strict mode when the ledger can't be matched cleanly."""


class OversellError(LotError):
    pass


class LotLinkError(LotError):
    pass


@dataclass(frozen=True)
class TxnRow:
    """Minimal float projection of a transaction row."""

    id: int
    account_id: int
    asset_id: int | None
    type: str
    ts: datetime
    quantity: float
    price: float | None
    fees: float
    currency: str
    lot_links: list[dict] | None = None


@dataclass
class Lot:
    buy_transaction_id: int
    account_id: int
    asset_id: int
    opened_at: datetime
    remaining: float
    original_quantity: float
    # buy fee is capitalized into the basis: (qty * price + fees) / qty
    cost_per_unit: float
    currency: str


@dataclass(frozen=True)
class LotMatch:
    buy_transaction_id: int
    quantity: float
    cost_per_unit: float


@dataclass(frozen=True)
class RealizedSale:
    sell_transaction_id: int
    account_id: int
    asset_id: int
    ts: datetime
    matches: tuple[LotMatch, ...]
    proceeds: float
    cost_basis: float
    realized_pnl: float
    currency: str


@dataclass
class LotState:
    # (account_id, asset_id) -> lots in FIFO order (oldest first)
    open_lots: dict[tuple[int, int], list[Lot]] = field(default_factory=dict)
    realized: list[RealizedSale] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)


# float comparisons: quantities smaller than this are "all gone"
EPSILON = 1e-9


def build_lots(transactions: Sequence[TxnRow], *, strict: bool = False) -> LotState:
    """Replay the ledger in (ts, id) order and derive open lots + realized P&L.

    Non-strict (the read path): an oversell consumes what exists and records a
    warning — one bad row must not blank the whole portfolio. Strict (the
    validation path): the same situation raises so the API can reject the write.
    """
    state = LotState()
    for txn in sorted(transactions, key=lambda t: (t.ts, t.id)):
        if txn.type in ("buy", "transfer_in"):
            _open_lot(state, txn)
        elif txn.type in ("sell", "transfer_out"):
            _consume(state, txn, strict=strict)
    return state


def average_cost(lots: Sequence[Lot]) -> float | None:
    quantity = sum(lot.remaining for lot in lots)
    if quantity <= EPSILON:
        return None
    return sum(lot.remaining * lot.cost_per_unit for lot in lots) / quantity


def _open_lot(state: LotState, txn: TxnRow) -> None:
    assert txn.asset_id is not None  # guaranteed by ck_transactions_asset_required
    # transfer_in carries its original cost in `price` so basis survives moves
    # between accounts; without one the position exists but has zero basis
    price = txn.price if txn.price is not None else 0.0
    cost = (txn.quantity * price + (txn.fees if txn.type == "buy" else 0.0)) / txn.quantity
    key = (txn.account_id, txn.asset_id)
    state.open_lots.setdefault(key, []).append(
        Lot(
            buy_transaction_id=txn.id,
            account_id=txn.account_id,
            asset_id=txn.asset_id,
            opened_at=txn.ts,
            remaining=txn.quantity,
            original_quantity=txn.quantity,
            cost_per_unit=cost,
            currency=txn.currency,
        )
    )


def _consume(state: LotState, txn: TxnRow, *, strict: bool) -> None:
    assert txn.asset_id is not None
    key = (txn.account_id, txn.asset_id)
    lots = state.open_lots.get(key, [])

    if txn.lot_links:
        matches = _consume_specific(state, txn, lots, strict=strict)
    else:
        matches = _consume_fifo(state, txn, lots, strict=strict)

    state.open_lots[key] = [lot for lot in lots if lot.remaining > EPSILON]

    if txn.type == "sell" and matches:
        sold = sum(m.quantity for m in matches)
        cost_basis = sum(m.quantity * m.cost_per_unit for m in matches)
        price = txn.price if txn.price is not None else 0.0
        proceeds = sold * price - txn.fees
        state.realized.append(
            RealizedSale(
                sell_transaction_id=txn.id,
                account_id=txn.account_id,
                asset_id=txn.asset_id,
                ts=txn.ts,
                matches=tuple(matches),
                proceeds=proceeds,
                cost_basis=cost_basis,
                realized_pnl=proceeds - cost_basis,
                currency=txn.currency,
            )
        )


def _consume_fifo(
    state: LotState, txn: TxnRow, lots: list[Lot], *, strict: bool
) -> list[LotMatch]:
    matches: list[LotMatch] = []
    needed = txn.quantity
    for lot in lots:
        if needed <= EPSILON:
            break
        take = min(lot.remaining, needed)
        if take > EPSILON:
            matches.append(LotMatch(lot.buy_transaction_id, take, lot.cost_per_unit))
            lot.remaining -= take
            needed -= take
    if needed > EPSILON:
        if strict:
            raise OversellError(
                f"transaction {txn.id} sells {txn.quantity:g} but only "
                f"{txn.quantity - needed:g} units are held"
            )
        state.warnings.append(
            {
                "transaction_id": txn.id,
                "account_id": txn.account_id,
                "asset_id": txn.asset_id,
                "unmatched_quantity": needed,
                "warning": "sell exceeds held quantity; matched what exists",
            }
        )
    return matches


def _consume_specific(
    state: LotState, txn: TxnRow, lots: list[Lot], *, strict: bool
) -> list[LotMatch]:
    """Specific-ID: the sell names its buy lots. Any invalid link falls back to
    FIFO for the whole transaction (with a warning) rather than half-applying."""
    by_id = {lot.buy_transaction_id: lot for lot in lots}
    links = txn.lot_links or []
    planned: list[tuple[Lot, float]] = []
    total = 0.0
    problem: str | None = None

    for link in links:
        lot = by_id.get(link.get("buy_transaction_id"))
        quantity = float(link.get("quantity", 0))
        if lot is None:
            problem = f"lot {link.get('buy_transaction_id')} is not an open buy lot"
        elif quantity <= EPSILON:
            problem = "lot link quantity must be positive"
        elif quantity > lot.remaining + EPSILON:
            problem = (
                f"lot {lot.buy_transaction_id} has {lot.remaining:g} remaining, "
                f"link asks for {quantity:g}"
            )
        if problem:
            break
        planned.append((lot, quantity))
        total += quantity

    if problem is None and abs(total - txn.quantity) > EPSILON:
        problem = f"lot links cover {total:g} units, transaction sells {txn.quantity:g}"

    if problem is not None:
        if strict:
            raise LotLinkError(f"transaction {txn.id}: {problem}")
        state.warnings.append(
            {
                "transaction_id": txn.id,
                "account_id": txn.account_id,
                "asset_id": txn.asset_id,
                "warning": f"invalid lot links ({problem}); fell back to first-in-first-out",
            }
        )
        return _consume_fifo(state, txn, lots, strict=strict)

    matches = []
    for lot, quantity in planned:
        matches.append(LotMatch(lot.buy_transaction_id, quantity, lot.cost_per_unit))
        lot.remaining -= quantity
    return matches
