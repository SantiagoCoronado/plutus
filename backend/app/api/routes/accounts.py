from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models import Account, BankInvestment, Transaction
from app.portfolio.valuation import cash_balances, to_txn_rows
from app.schemas.portfolio import AccountIn, AccountOut, AccountPatch, CashBalanceOut

router = APIRouter(prefix="/accounts", tags=["portfolio"])


def _get_account_or_404(db: Session, account_id: int) -> Account:
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="account not found")
    return account


def _to_out(
    account: Account,
    cash: dict[tuple[int, str], float],
    txn_counts: dict[int, int],
    inv_counts: dict[int, int],
) -> AccountOut:
    out = AccountOut.model_validate(account)
    out.cash_balances = [
        CashBalanceOut(currency=currency, amount=round(amount, 2))
        for (account_id, currency), amount in sorted(cash.items())
        if account_id == account.id
    ]
    out.transactions_count = txn_counts.get(account.id, 0)
    out.bank_investments_count = inv_counts.get(account.id, 0)
    return out


def _context(db: Session):
    txns = to_txn_rows(db.scalars(select(Transaction)).all())
    cash = cash_balances(txns)
    txn_counts = dict(
        db.execute(select(Transaction.account_id, func.count()).group_by(Transaction.account_id))
        .tuples()
        .all()
    )
    inv_counts = dict(
        db.execute(
            select(BankInvestment.account_id, func.count()).group_by(BankInvestment.account_id)
        )
        .tuples()
        .all()
    )
    return cash, txn_counts, inv_counts


@router.get("", response_model=list[AccountOut])
def list_accounts(db: Session = Depends(get_db)):
    accounts = db.scalars(select(Account).order_by(Account.name)).all()
    cash, txn_counts, inv_counts = _context(db)
    return [_to_out(a, cash, txn_counts, inv_counts) for a in accounts]


@router.post("", response_model=AccountOut, status_code=201)
def create_account(body: AccountIn, db: Session = Depends(get_db)):
    account = Account(**body.model_dump())
    db.add(account)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="account name already exists") from None
    db.refresh(account)
    return AccountOut.model_validate(account)


@router.get("/{account_id}", response_model=AccountOut)
def get_account(account_id: int, db: Session = Depends(get_db)):
    account = _get_account_or_404(db, account_id)
    cash, txn_counts, inv_counts = _context(db)
    return _to_out(account, cash, txn_counts, inv_counts)


@router.put("/{account_id}", response_model=AccountOut)
def update_account(account_id: int, body: AccountIn, db: Session = Depends(get_db)):
    account = _get_account_or_404(db, account_id)
    for key, value in body.model_dump().items():
        setattr(account, key, value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="account name already exists") from None
    db.refresh(account)
    cash, txn_counts, inv_counts = _context(db)
    return _to_out(account, cash, txn_counts, inv_counts)


@router.patch("/{account_id}", response_model=AccountOut)
def patch_account(account_id: int, body: AccountPatch, db: Session = Depends(get_db)):
    account = _get_account_or_404(db, account_id)
    if body.is_active is not None:
        account.is_active = body.is_active
    db.commit()
    db.refresh(account)
    cash, txn_counts, inv_counts = _context(db)
    return _to_out(account, cash, txn_counts, inv_counts)


@router.delete("/{account_id}", status_code=204)
def delete_account(account_id: int, db: Session = Depends(get_db)):
    account = _get_account_or_404(db, account_id)
    db.delete(account)  # transactions + bank investments cascade
    db.commit()
