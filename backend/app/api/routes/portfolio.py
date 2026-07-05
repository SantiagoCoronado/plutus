from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import get_settings
from app.portfolio.fx import SUPPORTED_CURRENCIES
from app.portfolio.valuation import allocation, compute_positions, performance_report
from app.schemas.portfolio import AllocationOut, PerformanceOut, PositionsReportOut

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

Period = Literal["1m", "3m", "6m", "ytd", "1y", "all"]


def _currency_or_422(currency: str | None) -> str:
    resolved = (currency or get_settings().base_currency).upper()
    if resolved not in SUPPORTED_CURRENCIES:
        raise HTTPException(
            status_code=422,
            detail={
                "errors": [
                    {
                        "path": "currency",
                        "error": f"unsupported currency (use one of {SUPPORTED_CURRENCIES})",
                    }
                ]
            },
        )
    return resolved


@router.get("/positions", response_model=PositionsReportOut)
def get_positions(
    db: Session = Depends(get_db),
    currency: str | None = None,
    account_id: int | None = None,
    as_of: date | None = None,
):
    return compute_positions(
        db,
        as_of=as_of or date.today(),
        currency=_currency_or_422(currency),
        account_id=account_id,
    )


@router.get("/performance", response_model=PerformanceOut)
def get_performance(
    db: Session = Depends(get_db),
    period: Period = Query(default="1y"),
    currency: str | None = None,
    account_id: int | None = None,
    benchmark: str | None = None,
):
    return performance_report(
        db,
        period=period,
        currency=_currency_or_422(currency),
        account_id=account_id,
        benchmark_symbol=benchmark,
    )


@router.get("/allocation", response_model=AllocationOut)
def get_allocation(
    db: Session = Depends(get_db),
    currency: str | None = None,
    by: Literal["asset_class", "currency", "account"] = Query(default="asset_class"),
):
    return allocation(db, as_of=date.today(), currency=_currency_or_422(currency), by=by)
