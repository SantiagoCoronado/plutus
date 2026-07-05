"""Strategy-from-content translator endpoints (spec §13.5).

POST /translations runs the LLM translation and returns the draft with its
fidelity report. POST /translations/{id}/confirm is THE ONLY path from a
draft to a running backtest — the user must see the report first.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.llm.base import LLMError
from app.llm.budget import BudgetExceeded
from app.llm.translator import translate_strategy_content
from app.models import StrategyTranslation
from app.schemas.translation import TranslationConfirmOut, TranslationIn, TranslationOut

router = APIRouter(prefix="/translations", tags=["translations"])


def _get_or_404(db: Session, translation_id: int) -> StrategyTranslation:
    translation = db.get(StrategyTranslation, translation_id)
    if translation is None:
        raise HTTPException(status_code=404, detail="translation not found")
    return translation


@router.post("", response_model=TranslationOut, status_code=201)
async def create_translation(body: TranslationIn, db: Session = Depends(get_db)):
    try:
        return await translate_strategy_content(db, body.content, body.symbol)
    except BudgetExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("", response_model=list[TranslationOut])
def list_translations(
    db: Session = Depends(get_db), limit: int = Query(default=20, ge=1, le=100)
):
    return db.scalars(
        select(StrategyTranslation)
        .order_by(StrategyTranslation.created_at.desc(), StrategyTranslation.id.desc())
        .limit(limit)
    ).all()


@router.get("/{translation_id}", response_model=TranslationOut)
def get_translation(translation_id: int, db: Session = Depends(get_db)):
    return _get_or_404(db, translation_id)


@router.post("/{translation_id}/confirm", response_model=TranslationConfirmOut,
             status_code=201)
def confirm_translation(translation_id: int, db: Session = Depends(get_db)):
    translation = _get_or_404(db, translation_id)
    if translation.status != "draft":
        raise HTTPException(status_code=409,
                            detail=f"translation is {translation.status}, not draft")
    if not translation.translatable or not translation.spec:
        raise HTTPException(status_code=422,
                            detail="this content was not translatable into a backtest")
    if translation.asset_id is None:
        raise HTTPException(
            status_code=422,
            detail=f"'{translation.symbol}' is not a tracked asset — track it first, "
            "then confirm",
        )

    from app.api.routes.backtests import create_strategy_backtest
    from app.schemas.backtest import StrategyBacktestIn

    spec = dict(translation.spec)
    try:
        body = StrategyBacktestIn(asset_id=translation.asset_id, **spec)
    except Exception as exc:
        raise HTTPException(status_code=422,
                            detail=f"stored spec failed validation: {exc}") from exc
    backtest = create_strategy_backtest(body, db)  # validates ASTs + enqueues

    translation.backtest_id = backtest.id
    translation.status = "confirmed"
    db.commit()
    return TranslationConfirmOut(translation_id=translation.id, backtest_id=backtest.id)


@router.post("/{translation_id}/discard", response_model=TranslationOut)
def discard_translation(translation_id: int, db: Session = Depends(get_db)):
    translation = _get_or_404(db, translation_id)
    if translation.status not in ("draft", "failed"):
        raise HTTPException(status_code=409,
                            detail=f"translation is {translation.status}")
    translation.status = "discarded"
    db.commit()
    db.refresh(translation)
    return translation
