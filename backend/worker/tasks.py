from app.core.logging import configure_logging
from app.ingestion.eod import run_asset_backfill, run_eod_all, run_eod_ingestion
from worker.celery_app import celery_app

configure_logging()

# Ingestion tasks sleep inside the provider token bucket (Tiingo ~80s/symbol at
# ~100 stocks ≈ 2.3h/night) — they need far more than the global 30-min limit.
INGEST_LIMITS = {"time_limit": 14_400, "soft_time_limit": 14_100}


@celery_app.task(name="worker.tasks.ingest_eod", **INGEST_LIMITS)
def ingest_eod(asset_class: str) -> int:
    """Nightly EOD job for one asset-class group; returns ingestion_runs.id."""
    return run_eod_ingestion(asset_class)


@celery_app.task(name="worker.tasks.ingest_eod_all", **INGEST_LIMITS)
def ingest_eod_all() -> list[int]:
    """Manual full ingestion (POST /api/v1/ingestion/run)."""
    return run_eod_all()


@celery_app.task(name="worker.tasks.backfill_asset", **INGEST_LIMITS)
def backfill_asset(asset_id: int) -> int:
    """History backfill for a newly tracked asset (POST /api/v1/assets)."""
    return run_asset_backfill(asset_id)


@celery_app.task(name="worker.tasks.seed_universe", **INGEST_LIMITS)
def seed_universe() -> int:
    """Seed the starter universe and backfill/deepen every active asset."""
    from app.ingestion.universe import run_universe_backfill, seed_universe_assets

    seed_universe_assets()
    return run_universe_backfill()


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
