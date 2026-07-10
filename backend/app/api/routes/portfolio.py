from datetime import date
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import get_settings
from app.models import Account
from app.portfolio.csv_import import TARGET_FIELDS, commit_rows, parse_preview
from app.portfolio.fx import SUPPORTED_CURRENCIES
from app.portfolio.valuation import allocation, compute_positions, performance_report
from app.schemas.portfolio import (
    AllocationOut,
    CsvCommitIn,
    CsvCommitOut,
    CsvPreviewIn,
    CsvPreviewOut,
    PerformanceOut,
    PositionsReportOut,
)

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


@router.post("/import/csv/preview", response_model=CsvPreviewOut)
def preview_csv(body: CsvPreviewIn):
    preview = parse_preview(body.content)
    if not any(preview.columns):
        raise HTTPException(
            status_code=422,
            detail={"errors": [{"path": "content", "error": "no header row found"}]},
        )
    return preview


@router.post("/import/csv/commit", response_model=CsvCommitOut)
def commit_csv(body: CsvCommitIn, db: Session = Depends(get_db)):
    errors = []
    if db.get(Account, body.account_id) is None:
        errors.append({"path": "account_id", "error": "account not found"})
    unknown = [target for target in body.mapping if target not in TARGET_FIELDS]
    if unknown:
        errors.append(
            {
                "path": "mapping",
                "error": f"unknown mapping targets {unknown} (valid: {list(TARGET_FIELDS)})",
            }
        )
    required = {"ts", "type", "quantity"} - set(body.mapping)
    if required:
        errors.append({"path": "mapping", "error": f"mapping must include {sorted(required)}"})
    tz = body.tz or get_settings().tz
    try:
        ZoneInfo(tz)
    except ZoneInfoNotFoundError:
        errors.append({"path": "tz", "error": f"unknown timezone '{tz}'"})
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})

    return commit_rows(
        db,
        account_id=body.account_id,
        content=body.content,
        mapping=body.mapping,
        tz=tz,
        number_format=body.number_format,
        date_order=body.date_order,
    )
