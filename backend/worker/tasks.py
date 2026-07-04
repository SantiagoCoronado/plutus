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


# 32 assets x 6 FMP calls at 8/min ~= 24 min — needs more than the global 30-min cap
@celery_app.task(name="worker.tasks.refresh_fundamentals", **INGEST_LIMITS)
def refresh_fundamentals() -> int:
    """Weekly fundamentals refresh for stock/ETF assets (stalest-first, budget-capped)."""
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


@celery_app.task(name="worker.tasks.run_backtest")
def run_backtest(backtest_id: int) -> int:
    """Execute a queued backtest row (screen or strategy); the UI polls the row."""
    from app.backtest.runner import execute_backtest

    return execute_backtest(backtest_id)


@celery_app.task(name="worker.tasks.run_scan")
def run_scan(scan_id: int) -> int:
    """Execute a queued mandate scan; the UI polls the row."""
    from app.discovery.runner import execute_scan

    return execute_scan(scan_id)


@celery_app.task(name="worker.tasks.dispatch_scans")
def dispatch_scans() -> list[int]:
    """Beat dispatcher: enqueue scans for active mandates whose cron has come due."""
    from app.discovery.runner import dispatch_due_mandates

    return dispatch_due_mandates()


@celery_app.task(name="worker.tasks.send_alert_digest")
def send_alert_digest() -> int:
    """Daily summary of new candidates for mandates set to digest mode."""
    from app.discovery.notify import send_digest

    return send_digest()
