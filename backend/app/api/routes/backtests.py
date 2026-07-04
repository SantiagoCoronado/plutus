from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models import Asset, Backtest, Screen
from app.schemas.backtest import (
    BacktestOut,
    BacktestSummaryOut,
    ScreenBacktestIn,
    StrategyBacktestIn,
)
from app.screener.ast import BACKTESTABLE_FIELDS, AstError, parse_ast

router = APIRouter(prefix="/backtests", tags=["backtests"])


def _parse_backtestable_or_422(raw: dict, *, context: str) -> None:
    try:
        parse_ast(raw, allowed_fields=BACKTESTABLE_FIELDS)
    except AstError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "context": context,
                "errors": exc.errors,
                "backtestable_fields": sorted(BACKTESTABLE_FIELDS),
            },
        ) from exc


def _get_backtest_or_404(db: Session, backtest_id: int) -> Backtest:
    backtest = db.get(Backtest, backtest_id)
    if backtest is None:
        raise HTTPException(status_code=404, detail="backtest not found")
    return backtest


def _enqueue(db: Session, backtest: Backtest) -> Backtest:
    db.add(backtest)
    db.commit()
    db.refresh(backtest)
    try:
        from worker.tasks import run_backtest

        run_backtest.delay(backtest.id)
    except Exception as exc:
        db.delete(backtest)
        db.commit()
        raise HTTPException(status_code=503, detail="could not enqueue backtest") from exc
    return backtest


@router.post("/screen", response_model=BacktestSummaryOut, status_code=201)
def create_screen_backtest(body: ScreenBacktestIn, db: Session = Depends(get_db)):
    if body.screen_id is not None:
        screen = db.get(Screen, body.screen_id)
        if screen is None:
            raise HTTPException(status_code=404, detail="screen not found")
        ast, asset_class = screen.ast, body.asset_class or screen.asset_class
    else:
        ast, asset_class = body.ast, body.asset_class

    if asset_class is None:
        raise HTTPException(
            status_code=422,
            detail={
                "errors": [
                    {
                        "path": "asset_class",
                        "error": "screen backtests need an asset_class "
                        "(one trading calendar per run)",
                    }
                ]
            },
        )
    _parse_backtestable_or_422(ast, context="screen ast")

    params = {
        "ast": ast,
        "asset_class": asset_class,
        "holding_days": body.holding_days,
        "start": body.start.isoformat() if body.start else None,
        "end": body.end.isoformat() if body.end else None,
        "benchmark": body.benchmark,
        "fees_pct": body.fees_pct,
    }
    backtest = Backtest(kind="screen", screen_id=body.screen_id, params=params)
    return _enqueue(db, backtest)


@router.post("/strategy", response_model=BacktestSummaryOut, status_code=201)
def create_strategy_backtest(body: StrategyBacktestIn, db: Session = Depends(get_db)):
    asset = db.get(Asset, body.asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="asset not found")

    from app.backtest.strategy import STRATEGY_FIELDS, parse_condition

    for context, condition in (("entry", body.entry), ("exit", body.exit)):
        try:
            parse_condition(condition)
        except AstError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "context": context,
                    "errors": exc.errors,
                    "valid_fields": sorted(STRATEGY_FIELDS),
                },
            ) from exc

    params = {
        "asset_id": body.asset_id,
        "symbol": asset.symbol,
        "entry": body.entry,
        "exit": body.exit,
        "stop_loss_pct": body.stop_loss_pct,
        "take_profit_pct": body.take_profit_pct,
        "position_size_pct": body.position_size_pct,
        "cash": body.cash,
        "fees_pct": body.fees_pct,
        "start": body.start.isoformat() if body.start else None,
        "end": body.end.isoformat() if body.end else None,
    }
    backtest = Backtest(kind="strategy", params=params)
    return _enqueue(db, backtest)


@router.get("", response_model=list[BacktestSummaryOut])
def list_backtests(
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    kind: str | None = Query(default=None, pattern="^(screen|strategy)$"),
):
    stmt = select(Backtest).order_by(Backtest.created_at.desc(), Backtest.id.desc()).limit(limit)
    if kind is not None:
        stmt = stmt.where(Backtest.kind == kind)
    return db.scalars(stmt).all()


@router.get("/{backtest_id}", response_model=BacktestOut)
def get_backtest(backtest_id: int, db: Session = Depends(get_db)):
    return _get_backtest_or_404(db, backtest_id)


@router.delete("/{backtest_id}", status_code=204)
def delete_backtest(backtest_id: int, db: Session = Depends(get_db)):
    backtest = _get_backtest_or_404(db, backtest_id)
    if backtest.artifact_path:
        Path(backtest.artifact_path).unlink(missing_ok=True)
    db.delete(backtest)
    db.commit()


@router.get("/{backtest_id}/report")
def get_backtest_report(backtest_id: int, db: Session = Depends(get_db)):
    backtest = _get_backtest_or_404(db, backtest_id)
    if not backtest.artifact_path or not Path(backtest.artifact_path).is_file():
        raise HTTPException(status_code=404, detail="no report artifact for this backtest")
    return FileResponse(backtest.artifact_path, media_type="text/html")
