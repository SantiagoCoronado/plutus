from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models import Account, BankInvestment
from app.portfolio.interest import (
    Terms,
    accrued_interest,
    current_value,
    effective_annual_rate,
    projected_maturity_value,
)
from app.schemas.portfolio import BankInvestmentIn, BankInvestmentOut

router = APIRouter(prefix="/bank-investments", tags=["portfolio"])


def _get_investment_or_404(db: Session, investment_id: int) -> BankInvestment:
    investment = db.get(BankInvestment, investment_id)
    if investment is None:
        raise HTTPException(status_code=404, detail="bank investment not found")
    return investment


def _validate_or_422(db: Session, body: BankInvestmentIn) -> None:
    errors: list[dict] = []

    account = db.get(Account, body.account_id)
    if account is None:
        errors.append({"path": "account_id", "error": "account not found"})
    elif account.type != "bank":
        errors.append(
            {"path": "account_id", "error": "bank investments belong to accounts of type 'bank'"}
        )

    if body.kind == "fixed_term" and body.term_days is None:
        errors.append({"path": "term_days", "error": "fixed-term investments need a term"})

    if body.rate_tiers is not None:
        if not body.rate_tiers:
            errors.append({"path": "rate_tiers", "error": "provide at least one tier or omit"})
        previous = 0.0
        for i, tier in enumerate(body.rate_tiers):
            if tier.up_to is None:
                if i != len(body.rate_tiers) - 1:
                    errors.append(
                        {
                            "path": f"rate_tiers.{i}.up_to",
                            "error": "only the last tier may leave up_to empty",
                        }
                    )
            elif tier.up_to <= previous:
                errors.append(
                    {"path": f"rate_tiers.{i}.up_to", "error": "tiers must be ascending"}
                )
            else:
                previous = tier.up_to

    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})


def terms_for(investment: BankInvestment) -> Terms:
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


def to_out(investment: BankInvestment, account_name: str | None = None) -> BankInvestmentOut:
    out = BankInvestmentOut.model_validate(investment)
    terms = terms_for(investment)
    today = date.today()
    out.accrued_interest = round(accrued_interest(terms, today), 2)
    out.current_value = round(current_value(terms, today), 2)
    projected = projected_maturity_value(terms)
    out.projected_maturity_value = round(projected, 2) if projected is not None else None
    out.effective_annual_rate = round(effective_annual_rate(terms), 6)
    if investment.maturity_date is not None and investment.status == "active":
        out.days_to_maturity = (investment.maturity_date - today).days
    out.account_name = account_name
    return out


def _apply(investment: BankInvestment, body: BankInvestmentIn) -> None:
    investment.account_id = body.account_id
    investment.name = body.name
    investment.kind = body.kind
    investment.principal = body.principal
    investment.currency = body.currency
    investment.annual_rate = body.annual_rate
    investment.rate_tiers = (
        [tier.model_dump() for tier in body.rate_tiers] if body.rate_tiers else None
    )
    investment.day_count = body.day_count
    investment.compounding = body.compounding
    investment.start_date = body.start_date
    investment.term_days = body.term_days
    investment.maturity_date = (
        body.start_date + timedelta(days=body.term_days) if body.term_days else None
    )
    investment.cap_amount = body.cap_amount
    investment.auto_renew = body.auto_renew
    investment.status = body.status
    investment.note = body.note


@router.get("", response_model=list[BankInvestmentOut])
def list_bank_investments(db: Session = Depends(get_db), account_id: int | None = None):
    query = (
        select(BankInvestment, Account.name)
        .join(Account, Account.id == BankInvestment.account_id)
        .order_by(BankInvestment.maturity_date.nulls_last(), BankInvestment.name)
    )
    if account_id is not None:
        query = query.where(BankInvestment.account_id == account_id)
    return [to_out(inv, account_name) for inv, account_name in db.execute(query).all()]


@router.post("", response_model=BankInvestmentOut, status_code=201)
def create_bank_investment(body: BankInvestmentIn, db: Session = Depends(get_db)):
    _validate_or_422(db, body)
    investment = BankInvestment()
    _apply(investment, body)
    db.add(investment)
    db.commit()
    db.refresh(investment)
    account = db.get(Account, investment.account_id)
    return to_out(investment, account.name if account else None)


@router.get("/{investment_id}", response_model=BankInvestmentOut)
def get_bank_investment(investment_id: int, db: Session = Depends(get_db)):
    investment = _get_investment_or_404(db, investment_id)
    account = db.get(Account, investment.account_id)
    return to_out(investment, account.name if account else None)


@router.put("/{investment_id}", response_model=BankInvestmentOut)
def update_bank_investment(
    investment_id: int, body: BankInvestmentIn, db: Session = Depends(get_db)
):
    investment = _get_investment_or_404(db, investment_id)
    _validate_or_422(db, body)
    _apply(investment, body)
    db.commit()
    db.refresh(investment)
    account = db.get(Account, investment.account_id)
    return to_out(investment, account.name if account else None)


@router.delete("/{investment_id}", status_code=204)
def delete_bank_investment(investment_id: int, db: Session = Depends(get_db)):
    investment = _get_investment_or_404(db, investment_id)
    db.delete(investment)
    db.commit()
