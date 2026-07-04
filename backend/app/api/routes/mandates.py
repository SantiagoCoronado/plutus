from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.discovery.schedule import next_run_at, parse_cron
from app.discovery.signals import SIGNALS
from app.models import Candidate, Mandate, Scan, Watchlist
from app.schemas.discovery import (
    LastScanOut,
    MandateIn,
    MandateOut,
    MandatePatch,
    MandateStatsOut,
    ScanOut,
    SignalInfoOut,
)
from app.screener.ast import SCREEN_FIELDS, AstError, parse_ast

router = APIRouter(prefix="/mandates", tags=["mandates"])


def _validate_or_422(db: Session, body: MandateIn) -> None:
    errors: list[dict] = []

    if body.rules is not None:
        try:
            parse_ast(body.rules, allowed_fields=SCREEN_FIELDS)
        except AstError as exc:
            errors.extend({**e, "path": f"rules.{e.get('path', '')}"} for e in exc.errors)

    try:
        parse_cron(body.schedule)
    except ValueError as exc:
        errors.append({"path": "schedule", "error": str(exc)})

    weighted = {key: w for key, w in body.score_weights.items() if w != 0}
    for key, weight in body.score_weights.items():
        spec = SIGNALS.get(key)
        if spec is None:
            errors.append(
                {
                    "path": f"score_weights.{key}",
                    "error": "unknown signal",
                    "valid_signals": sorted(SIGNALS),
                }
            )
        elif body.asset_class not in spec.asset_classes:
            errors.append(
                {
                    "path": f"score_weights.{key}",
                    "error": f"signal does not apply to {body.asset_class} assets",
                }
            )
        elif weight < 0:
            errors.append({"path": f"score_weights.{key}", "error": "weight must be >= 0"})
    if not any(w > 0 for w in weighted.values()):
        errors.append(
            {"path": "score_weights", "error": "at least one signal needs a weight above zero"}
        )

    universe = body.universe_def
    if universe.type == "watchlist" and db.get(Watchlist, universe.watchlist_id) is None:
        errors.append({"path": "universe_def.watchlist_id", "error": "watchlist not found"})

    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})


def _get_mandate_or_404(db: Session, mandate_id: int) -> Mandate:
    mandate = db.get(Mandate, mandate_id)
    if mandate is None:
        raise HTTPException(status_code=404, detail="mandate not found")
    return mandate


def _apply(mandate: Mandate, body: MandateIn) -> None:
    mandate.name = body.name
    mandate.description = body.description
    mandate.asset_class = body.asset_class
    mandate.universe_def = body.universe_def.model_dump()
    mandate.rules = body.rules
    mandate.schedule = body.schedule
    mandate.score_weights = body.score_weights
    mandate.min_score = body.min_score
    mandate.notify_min_score = body.notify_min_score
    mandate.max_candidates = body.max_candidates
    mandate.cooldown_days = body.cooldown_days
    mandate.notify = body.notify
    mandate.active = body.active


def _stats_by_mandate(db: Session) -> dict[int, MandateStatsOut]:
    rows = db.execute(
        select(Candidate.mandate_id, Candidate.status, func.count()).group_by(
            Candidate.mandate_id, Candidate.status
        )
    ).all()
    stats: dict[int, dict[str, int]] = {}
    for mandate_id, status, count in rows:
        stats.setdefault(mandate_id, {})[status] = count
    out: dict[int, MandateStatsOut] = {}
    for mandate_id, counts in stats.items():
        starred, dismissed = counts.get("starred", 0), counts.get("dismissed", 0)
        out[mandate_id] = MandateStatsOut(
            candidates_total=sum(counts.values()),
            new=counts.get("new", 0),
            starred=starred,
            dismissed=dismissed,
            hit_rate=starred / (starred + dismissed) if starred + dismissed else None,
        )
    return out


def _last_scan_by_mandate(db: Session) -> dict[int, Scan]:
    rows = db.scalars(
        select(Scan).order_by(Scan.mandate_id, Scan.created_at.desc(), Scan.id.desc()).distinct(
            Scan.mandate_id
        )
    ).all()
    return {scan.mandate_id: scan for scan in rows}


def _to_out(
    mandate: Mandate,
    stats: MandateStatsOut | None,
    last_scan: Scan | None,
) -> MandateOut:
    out = MandateOut.model_validate(mandate)
    try:
        out.next_run_at = next_run_at(
            mandate.schedule, mandate.last_run_at or mandate.created_at
        )
    except ValueError:
        out.next_run_at = None
    out.stats = stats or MandateStatsOut()
    out.last_scan = LastScanOut.model_validate(last_scan) if last_scan else None
    return out


# NOTE: literal paths must be declared before /{mandate_id} (screens.py precedent)


@router.get("/signals", response_model=list[SignalInfoOut])
def list_signals():
    return [
        SignalInfoOut(
            key=spec.key,
            label=spec.label,
            description=spec.description,
            asset_classes=list(spec.asset_classes),
            needs_volume=spec.requires_volume,
            supports_history_check=spec.supports_history_check,
        )
        for spec in SIGNALS.values()
    ]


@router.post("/test-alert")
def test_alert(db: Session = Depends(get_db)):
    """Send a test message to every configured channel — the SMTP verification hook."""
    from app.discovery.notify import send_test_alert

    results = send_test_alert(db)
    if not results:
        raise HTTPException(
            status_code=400,
            detail="no alert channel is configured — set SMTP_* or TELEGRAM_* in .env",
        )
    return {"results": results}


@router.get("", response_model=list[MandateOut])
def list_mandates(db: Session = Depends(get_db)):
    mandates = db.scalars(select(Mandate).order_by(Mandate.name)).all()
    stats = _stats_by_mandate(db)
    last_scans = _last_scan_by_mandate(db)
    return [_to_out(m, stats.get(m.id), last_scans.get(m.id)) for m in mandates]


@router.post("", response_model=MandateOut, status_code=201)
def create_mandate(body: MandateIn, db: Session = Depends(get_db)):
    _validate_or_422(db, body)
    mandate = Mandate()
    _apply(mandate, body)
    db.add(mandate)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="mandate name already exists") from None
    db.refresh(mandate)
    return _to_out(mandate, None, None)


@router.get("/{mandate_id}", response_model=MandateOut)
def get_mandate(mandate_id: int, db: Session = Depends(get_db)):
    mandate = _get_mandate_or_404(db, mandate_id)
    stats = _stats_by_mandate(db)
    last_scans = _last_scan_by_mandate(db)
    return _to_out(mandate, stats.get(mandate.id), last_scans.get(mandate.id))


@router.put("/{mandate_id}", response_model=MandateOut)
def update_mandate(mandate_id: int, body: MandateIn, db: Session = Depends(get_db)):
    mandate = _get_mandate_or_404(db, mandate_id)
    _validate_or_422(db, body)
    _apply(mandate, body)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="mandate name already exists") from None
    db.refresh(mandate)
    stats = _stats_by_mandate(db)
    last_scans = _last_scan_by_mandate(db)
    return _to_out(mandate, stats.get(mandate.id), last_scans.get(mandate.id))


@router.patch("/{mandate_id}", response_model=MandateOut)
def patch_mandate(mandate_id: int, body: MandatePatch, db: Session = Depends(get_db)):
    mandate = _get_mandate_or_404(db, mandate_id)
    if body.active is not None:
        mandate.active = body.active
    if body.notify is not None:
        mandate.notify = body.notify
    db.commit()
    db.refresh(mandate)
    stats = _stats_by_mandate(db)
    last_scans = _last_scan_by_mandate(db)
    return _to_out(mandate, stats.get(mandate.id), last_scans.get(mandate.id))


@router.delete("/{mandate_id}", status_code=204)
def delete_mandate(mandate_id: int, db: Session = Depends(get_db)):
    mandate = _get_mandate_or_404(db, mandate_id)
    db.delete(mandate)  # scans + candidates cascade
    db.commit()


@router.post("/{mandate_id}/scan", response_model=ScanOut, status_code=201)
def run_mandate_now(mandate_id: int, db: Session = Depends(get_db)):
    mandate = _get_mandate_or_404(db, mandate_id)
    in_flight = db.scalar(
        select(func.count())
        .select_from(Scan)
        .where(Scan.mandate_id == mandate.id, Scan.status.in_(("queued", "running")))
    )
    if in_flight:
        raise HTTPException(status_code=409, detail="a scan is already queued or running")

    scan = Scan(mandate_id=mandate.id)  # manual runs do not move the standing schedule
    db.add(scan)
    db.commit()
    db.refresh(scan)
    try:
        from worker.tasks import run_scan

        run_scan.delay(scan.id)
    except Exception as exc:
        db.delete(scan)
        db.commit()
        raise HTTPException(status_code=503, detail="could not enqueue scan") from exc
    return scan


@router.get("/{mandate_id}/scans", response_model=list[ScanOut])
def list_mandate_scans(
    mandate_id: int,
    db: Session = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=200),
):
    _get_mandate_or_404(db, mandate_id)
    return db.scalars(
        select(Scan)
        .where(Scan.mandate_id == mandate_id)
        .order_by(Scan.created_at.desc(), Scan.id.desc())
        .limit(limit)
    ).all()
