from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models import Asset, Candidate, Mandate
from app.schemas.discovery import (
    CandidateOut,
    CandidateStatusIn,
    CandidateSummaryOut,
    MandateCandidateSummaryOut,
)

router = APIRouter(prefix="/candidates", tags=["candidates"])

CANDIDATE_COLUMNS = (
    Candidate,
    Mandate.name.label("mandate_name"),
    Asset.symbol,
    Asset.name.label("asset_name"),
    Asset.asset_class,
)


def _to_out(row) -> CandidateOut:
    candidate: Candidate = row.Candidate
    return CandidateOut(
        id=candidate.id,
        mandate_id=candidate.mandate_id,
        mandate_name=row.mandate_name,
        asset_id=candidate.asset_id,
        symbol=row.symbol,
        name=row.asset_name,
        asset_class=row.asset_class,
        ts=candidate.ts,
        score=candidate.score,
        status=candidate.status,
        signals=candidate.signals,
        context=candidate.context,
        created_at=candidate.created_at,
    )


@router.get("", response_model=list[CandidateOut])
def list_candidates(
    db: Session = Depends(get_db),
    status: str | None = Query(default=None, pattern="^(new|reviewed|starred|dismissed)$"),
    mandate_id: int | None = None,
    asset_class: str | None = Query(default=None, pattern="^(stock|etf|crypto|forex)$"),
    order: str = Query(default="score", pattern="^(score|newest)$"),
    limit: int = Query(default=50, ge=1, le=200),
):
    stmt = (
        select(*CANDIDATE_COLUMNS)
        .join(Mandate, Mandate.id == Candidate.mandate_id)
        .join(Asset, Asset.id == Candidate.asset_id)
        .limit(limit)
    )
    if status is not None:
        stmt = stmt.where(Candidate.status == status)
    if mandate_id is not None:
        stmt = stmt.where(Candidate.mandate_id == mandate_id)
    if asset_class is not None:
        stmt = stmt.where(Asset.asset_class == asset_class)
    if order == "score":
        stmt = stmt.order_by(Candidate.score.desc(), Candidate.id.desc())
    else:
        stmt = stmt.order_by(Candidate.created_at.desc(), Candidate.id.desc())
    return [_to_out(row) for row in db.execute(stmt)]


@router.get("/summary", response_model=CandidateSummaryOut)
def candidate_summary(db: Session = Depends(get_db)):
    by_status = {status: 0 for status in ("new", "reviewed", "starred", "dismissed")}
    for status, count in db.execute(
        select(Candidate.status, func.count()).group_by(Candidate.status)
    ):
        by_status[status] = count

    rows = db.execute(
        select(Candidate.mandate_id, Mandate.name, Candidate.status, func.count())
        .join(Mandate, Mandate.id == Candidate.mandate_id)
        .group_by(Candidate.mandate_id, Mandate.name, Candidate.status)
    ).all()
    grouped: dict[int, dict] = {}
    for mandate_id, mandate_name, status, count in rows:
        entry = grouped.setdefault(
            mandate_id, {"mandate_id": mandate_id, "mandate_name": mandate_name}
        )
        entry[status] = count
    by_mandate = []
    for entry in grouped.values():
        starred, dismissed = entry.get("starred", 0), entry.get("dismissed", 0)
        by_mandate.append(
            MandateCandidateSummaryOut(
                mandate_id=entry["mandate_id"],
                mandate_name=entry["mandate_name"],
                new=entry.get("new", 0),
                starred=starred,
                dismissed=dismissed,
                hit_rate=starred / (starred + dismissed) if starred + dismissed else None,
            )
        )
    by_mandate.sort(key=lambda item: item.mandate_name)
    return CandidateSummaryOut(by_status=by_status, by_mandate=by_mandate)


@router.patch("/{candidate_id}", response_model=CandidateOut)
def update_candidate_status(
    candidate_id: int, body: CandidateStatusIn, db: Session = Depends(get_db)
):
    row = db.execute(
        select(*CANDIDATE_COLUMNS)
        .join(Mandate, Mandate.id == Candidate.mandate_id)
        .join(Asset, Asset.id == Candidate.asset_id)
        .where(Candidate.id == candidate_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="candidate not found")
    row.Candidate.status = body.status
    db.commit()
    return _to_out(row)
