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


@celery_app.task(name="worker.tasks.refresh_metrics", ignore_result=False)
def refresh_metrics(_prior_result=None) -> int:
    """Nightly asset_metrics materialization (also chained after manual ingestion)."""
    from app.analysis.metrics import run_metrics_refresh

    return run_metrics_refresh()


@celery_app.task(name="worker.tasks.refresh_fundamentals")
def refresh_fundamentals() -> int:
    """Weekly fundamentals refresh for stock/ETF assets."""
    from app.ingestion.fundamentals import run_fundamentals_refresh

    return run_fundamentals_refresh()


@celery_app.task(name="worker.tasks.refresh_fundamentals_asset")
def refresh_fundamentals_asset(asset_id: int) -> int:
    """On-demand fundamentals refresh for one asset."""
    from app.ingestion.fundamentals import run_fundamentals_refresh

    return run_fundamentals_refresh(asset_id=asset_id)


@celery_app.task(name="worker.tasks.pull_news")
def pull_news() -> int:
    """15-minute company-news pull for stock/ETF assets."""
    from app.ingestion.news import run_news_pull

    return run_news_pull()
