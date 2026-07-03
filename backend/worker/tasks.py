from app.core.logging import configure_logging
from app.ingestion.eod import run_asset_backfill, run_eod_all, run_eod_ingestion
from worker.celery_app import celery_app

configure_logging()


@celery_app.task(name="worker.tasks.ingest_eod")
def ingest_eod(asset_class: str) -> int:
    """Nightly EOD job for one asset-class group; returns ingestion_runs.id."""
    return run_eod_ingestion(asset_class)


@celery_app.task(name="worker.tasks.ingest_eod_all")
def ingest_eod_all() -> list[int]:
    """Manual full ingestion (POST /api/v1/ingestion/run)."""
    return run_eod_all()


@celery_app.task(name="worker.tasks.backfill_asset")
def backfill_asset(asset_id: int) -> int:
    """History backfill for a newly tracked asset (POST /api/v1/assets)."""
    return run_asset_backfill(asset_id)
