from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models import Screen
from app.schemas.screen import (
    ScreenFieldOut,
    ScreenHitOut,
    ScreenIn,
    ScreenOut,
    ScreenRunRequest,
    ScreenRunResult,
)
from app.screener.ast import BACKTESTABLE_FIELDS, NON_PIT_FIELDS, AstError, Node, parse_ast
from app.screener.sql import run_screen

router = APIRouter(prefix="/screens", tags=["screener"])


def _parse_or_422(raw: dict) -> Node:
    try:
        return parse_ast(raw)
    except AstError as exc:
        raise HTTPException(status_code=422, detail={"errors": exc.errors}) from exc


def _get_screen_or_404(db: Session, screen_id: int) -> Screen:
    screen = db.get(Screen, screen_id)
    if screen is None:
        raise HTTPException(status_code=404, detail="screen not found")
    return screen


def _run(db: Session, node: Node, asset_class: str | None, limit: int) -> ScreenRunResult:
    hits = run_screen(db, node, asset_class, limit=limit)
    columns = sorted({field for hit in hits for field in hit.values}) if hits else []
    return ScreenRunResult(
        count=len(hits),
        columns=columns,
        results=[ScreenHitOut.model_validate(hit, from_attributes=True) for hit in hits],
    )


@router.get("/fields", response_model=list[ScreenFieldOut])
def list_fields():
    from app.models import METRIC_COLUMNS

    return [
        ScreenFieldOut(
            name=name,
            backtestable=name in BACKTESTABLE_FIELDS,
            fundamental=name in NON_PIT_FIELDS,
        )
        for name in METRIC_COLUMNS
    ]


@router.get("", response_model=list[ScreenOut])
def list_screens(db: Session = Depends(get_db), limit: int = Query(default=100, ge=1, le=200)):
    return db.scalars(select(Screen).order_by(Screen.name).limit(limit)).all()


@router.post("", response_model=ScreenOut, status_code=201)
def create_screen(body: ScreenIn, db: Session = Depends(get_db)):
    _parse_or_422(body.ast)
    screen = Screen(
        name=body.name,
        description=body.description,
        asset_class=body.asset_class,
        ast=body.ast,
    )
    db.add(screen)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="screen name already exists") from exc
    db.refresh(screen)
    return screen


@router.get("/{screen_id}", response_model=ScreenOut)
def get_screen(screen_id: int, db: Session = Depends(get_db)):
    return _get_screen_or_404(db, screen_id)


@router.put("/{screen_id}", response_model=ScreenOut)
def update_screen(screen_id: int, body: ScreenIn, db: Session = Depends(get_db)):
    screen = _get_screen_or_404(db, screen_id)
    _parse_or_422(body.ast)
    screen.name = body.name
    screen.description = body.description
    screen.asset_class = body.asset_class
    screen.ast = body.ast
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="screen name already exists") from exc
    db.refresh(screen)
    return screen


@router.delete("/{screen_id}", status_code=204)
def delete_screen(screen_id: int, db: Session = Depends(get_db)):
    screen = _get_screen_or_404(db, screen_id)
    db.delete(screen)
    db.commit()


@router.post("/run", response_model=ScreenRunResult)
def run_adhoc_screen(body: ScreenRunRequest, db: Session = Depends(get_db)):
    node = _parse_or_422(body.ast)
    return _run(db, node, body.asset_class, body.limit)


@router.post("/{screen_id}/run", response_model=ScreenRunResult)
def run_saved_screen(
    screen_id: int,
    db: Session = Depends(get_db),
    limit: int = Query(default=200, ge=1, le=200),
):
    screen = _get_screen_or_404(db, screen_id)
    node = _parse_or_422(screen.ast)
    return _run(db, node, screen.asset_class, limit)
