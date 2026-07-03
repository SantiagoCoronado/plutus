from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models import IngestionRun
from app.schemas.ingestion import IngestionRunOut

router = APIRouter(prefix="/ingestion", tags=["ingestion"])


@router.get("/runs", response_model=list[IngestionRunOut])
def list_runs(
    limit: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    return db.scalars(
        select(IngestionRun).order_by(IngestionRun.started_at.desc(), IngestionRun.id.desc()).limit(limit)
    ).all()


@router.post("/run", status_code=202)
def trigger_run():
    """Manual full EOD ingestion — the verification path for the Phase 1 gate."""
    try:
        from worker.tasks import ingest_eod_all

        result = ingest_eod_all.delay()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503, detail=f"could not enqueue ingestion (is redis/worker up?): {exc}"
        ) from exc
    return {"task_id": result.id, "status": "queued"}
