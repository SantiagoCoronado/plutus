from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models import Account, Asset, Transaction
from app.models.transaction import ASSET_TRANSACTION_TYPES
from app.portfolio.lots import LotError, TxnRow, build_lots
from app.portfolio.valuation import to_txn_rows
from app.schemas.portfolio import TransactionIn, TransactionListOut, TransactionOut

router = APIRouter(prefix="/transactions", tags=["portfolio"])

# a buy or sell without a price would silently create zero-cost basis
PRICE_REQUIRED_TYPES = ("buy", "sell")


def _get_txn_or_404(db: Session, transaction_id: int) -> Transaction:
    txn = db.get(Transaction, transaction_id)
    if txn is None:
        raise HTTPException(status_code=404, detail="transaction not found")
    return txn


def _validate_or_422(db: Session, body: TransactionIn) -> None:
    errors: list[dict] = []

    if db.get(Account, body.account_id) is None:
        errors.append({"path": "account_id", "error": "account not found"})

    if body.type in ASSET_TRANSACTION_TYPES and body.asset_id is None:
        errors.append(
            {"path": "asset_id", "error": f"{body.type} transactions must reference an asset"}
        )
    if body.asset_id is not None and db.get(Asset, body.asset_id) is None:
        errors.append({"path": "asset_id", "error": "asset not found"})

    if body.type in PRICE_REQUIRED_TYPES and body.price is None:
        errors.append({"path": "price", "error": f"{body.type} transactions need a price"})

    if body.lot_links and body.type != "sell":
        errors.append({"path": "lot_links", "error": "lot links only apply to sells"})

    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})


def _body_to_row(body: TransactionIn, txn_id: int) -> TxnRow:
    return TxnRow(
        id=txn_id,
        account_id=body.account_id,
        asset_id=body.asset_id,
        type=body.type,
        ts=body.ts,
        quantity=body.quantity,
        price=body.price,
        fees=body.fees,
        currency=body.currency,
        lot_links=[link.model_dump() for link in body.lot_links] if body.lot_links else None,
    )


def _ledger_rows(
    db: Session, account_id: int, asset_id: int, *, exclude_id: int | None = None
) -> list[TxnRow]:
    query = select(Transaction).where(
        Transaction.account_id == account_id, Transaction.asset_id == asset_id
    )
    rows = to_txn_rows(db.scalars(query).all())
    if exclude_id is not None:
        rows = [row for row in rows if row.id != exclude_id]
    return rows


def _check_lots_or_error(rows: list[TxnRow]) -> str | None:
    try:
        build_lots(rows, strict=True)
    except LotError as exc:
        return str(exc)
    return None


def _validate_ledger_write(
    db: Session, body: TransactionIn, *, exclude_id: int | None = None
) -> None:
    """Strict lot replay for the affected (account, asset) ledger, with the new
    row included — rejects oversells and bad lot links before they persist."""
    if body.asset_id is None or body.type not in ASSET_TRANSACTION_TYPES:
        return
    # a provisional id larger than any real one keeps ts-tie ordering stable
    rows = _ledger_rows(db, body.account_id, body.asset_id, exclude_id=exclude_id)
    provisional_id = max((row.id for row in rows), default=0) + 1_000_000
    rows.append(_body_to_row(body, exclude_id or provisional_id))
    error = _check_lots_or_error(rows)
    if error:
        raise HTTPException(status_code=422, detail={"errors": [{"path": "", "error": error}]})


def _to_out(txn: Transaction, account_name: str | None, symbol: str | None) -> TransactionOut:
    out = TransactionOut.model_validate(txn)
    out.account_name = account_name
    out.symbol = symbol
    return out


def _apply(txn: Transaction, body: TransactionIn) -> None:
    txn.account_id = body.account_id
    txn.asset_id = body.asset_id
    txn.type = body.type
    txn.ts = body.ts
    txn.quantity = body.quantity
    txn.price = body.price
    txn.fees = body.fees
    txn.currency = body.currency
    txn.note = body.note
    txn.lot_links = (
        [link.model_dump() for link in body.lot_links] if body.lot_links else None
    )


@router.get("", response_model=TransactionListOut)
def list_transactions(
    db: Session = Depends(get_db),
    account_id: int | None = None,
    asset_id: int | None = None,
    type: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    query = select(Transaction)
    if account_id is not None:
        query = query.where(Transaction.account_id == account_id)
    if asset_id is not None:
        query = query.where(Transaction.asset_id == asset_id)
    if type is not None:
        query = query.where(Transaction.type == type)
    if start is not None:
        query = query.where(Transaction.ts >= start)
    if end is not None:
        query = query.where(Transaction.ts <= end)

    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(
        select(Transaction, Account.name, Asset.symbol)
        .join(Account, Account.id == Transaction.account_id)
        .join(Asset, Asset.id == Transaction.asset_id, isouter=True)
        .where(Transaction.id.in_(query.with_only_columns(Transaction.id)))
        .order_by(Transaction.ts.desc(), Transaction.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return TransactionListOut(
        items=[_to_out(txn, account_name, symbol) for txn, account_name, symbol in rows],
        total=total or 0,
    )


@router.post("", response_model=TransactionOut, status_code=201)
def create_transaction(body: TransactionIn, db: Session = Depends(get_db)):
    _validate_or_422(db, body)
    _validate_ledger_write(db, body)
    txn = Transaction()
    _apply(txn, body)
    db.add(txn)
    db.commit()
    db.refresh(txn)
    account = db.get(Account, txn.account_id)
    asset = db.get(Asset, txn.asset_id) if txn.asset_id else None
    return _to_out(txn, account.name if account else None, asset.symbol if asset else None)


@router.get("/{transaction_id}", response_model=TransactionOut)
def get_transaction(transaction_id: int, db: Session = Depends(get_db)):
    txn = _get_txn_or_404(db, transaction_id)
    account = db.get(Account, txn.account_id)
    asset = db.get(Asset, txn.asset_id) if txn.asset_id else None
    return _to_out(txn, account.name if account else None, asset.symbol if asset else None)


@router.put("/{transaction_id}", response_model=TransactionOut)
def update_transaction(transaction_id: int, body: TransactionIn, db: Session = Depends(get_db)):
    txn = _get_txn_or_404(db, transaction_id)
    _validate_or_422(db, body)
    _validate_ledger_write(db, body, exclude_id=txn.id)
    # the edit must also not orphan later sells on the *original* ledger
    # (e.g. shrinking a buy that a sell already consumes)
    if txn.asset_id is not None and (
        txn.asset_id != body.asset_id or txn.account_id != body.account_id
    ):
        remaining = _ledger_rows(db, txn.account_id, txn.asset_id, exclude_id=txn.id)
        error = _check_lots_or_error(remaining)
        if error:
            raise HTTPException(
                status_code=409,
                detail=f"editing this transaction would break a later sell: {error}",
            )
    _apply(txn, body)
    db.commit()
    db.refresh(txn)
    account = db.get(Account, txn.account_id)
    asset = db.get(Asset, txn.asset_id) if txn.asset_id else None
    return _to_out(txn, account.name if account else None, asset.symbol if asset else None)


@router.delete("/{transaction_id}", status_code=204)
def delete_transaction(transaction_id: int, db: Session = Depends(get_db)):
    txn = _get_txn_or_404(db, transaction_id)
    if txn.asset_id is not None:
        remaining = _ledger_rows(db, txn.account_id, txn.asset_id, exclude_id=txn.id)
        error = _check_lots_or_error(remaining)
        if error:
            raise HTTPException(
                status_code=409,
                detail=f"deleting this transaction would break a later sell: {error}",
            )
    db.delete(txn)
    db.commit()
