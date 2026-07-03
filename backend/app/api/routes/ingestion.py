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
    query = (
        select(IngestionRun)
        .order_by(IngestionRun.started_at.desc(), IngestionRun.id.desc())
        .limit(limit)
    )
    return db.scalars(query).all()


@router.post("/run", status_code=202)
def trigger_run():
    """Manual full pipeline: EOD ingestion chained into a metrics refresh."""
    try:
        from celery import chain

        from worker.tasks import ingest_eod_all, refresh_metrics

        result = chain(ingest_eod_all.s(), refresh_metrics.s()).apply_async()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503, detail=f"could not enqueue ingestion (is redis/worker up?): {exc}"
        ) from exc
    return {"task_id": result.id, "status": "queued"}
