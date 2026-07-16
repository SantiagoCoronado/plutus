from celery.signals import task_failure

from app.core.logging import configure_logging, get_logger
from app.ingestion.eod import run_asset_backfill, run_eod_all, run_eod_ingestion
from worker.celery_app import celery_app

configure_logging()
log = get_logger(__name__)


@task_failure.connect
def _notify_task_failure(sender=None, exception=None, **_kwargs):
    """Push one email/Telegram per (task, day) when any task raises (spec phase
    10 M3). Structured logs already record the traceback; this makes sure a
    failing nightly job is *pushed* at you instead of waiting to be noticed on
    the Settings page. Never raises — a broken channel must not mask the
    original failure or re-fail the task."""
    task_name = getattr(sender, "name", str(sender))
    try:
        from datetime import UTC, datetime

        from app.core.db import session_scope
        from app.discovery.notify import deliver
        from app.providers.registry import _shared_redis

        dedup_key = f"notify:task_failure:{task_name}:{datetime.now(UTC):%Y%m%d}"
        if not _shared_redis().set(dedup_key, "1", nx=True, ex=86400):
            return
        detail = f"{type(exception).__name__}: {exception}"[:500]
        with session_scope() as session:
            deliver(
                session,
                "task_failure",
                f"Task failed: {task_name}",
                f"{detail}\n\nFurther failures of this task today are logged but "
                "not re-notified. See the worker logs for the traceback.",
                {"task": task_name},
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("task_failure_notify_failed", task=task_name, error=str(exc))

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


@celery_app.task(name="worker.tasks.check_maturities")
def check_maturities() -> int:
    """Flip matured bank investments and remind about upcoming maturities.
    Locked: a concurrent duplicate run could roll the same investment twice."""
    from app.core.locks import redis_lock
    from app.portfolio.maturities import run_maturity_check
    from app.providers.registry import _shared_redis

    with redis_lock(_shared_redis(), "bank:maturities", ttl_seconds=300) as acquired:
        if not acquired:
            log.info("check_maturities skipped: another run holds the lock")
            return -1
        return run_maturity_check()


@celery_app.task(name="worker.tasks.run_agent_deep_dive", time_limit=900, soft_time_limit=840)
def run_agent_deep_dive(conversation_id: int) -> int:
    """Execute a queued AI deep-dive task conversation; the UI polls the row."""
    from app.llm.research import run_deep_dive

    return run_deep_dive(conversation_id)


@celery_app.task(
    name="worker.tasks.run_nightly_research_memos", time_limit=3600, soft_time_limit=3480
)
def run_nightly_research_memos() -> list[int]:
    """Nightly beat: AI research memos for the best new candidates above threshold."""
    from app.llm.research import run_nightly_memos

    return run_nightly_memos()


@celery_app.task(
    name="worker.tasks.evaluate_price_alerts", time_limit=120, soft_time_limit=100
)
def evaluate_price_alerts() -> dict:
    """Per-minute beat: fire any armed price alert whose live quote just crossed
    its threshold. Reads quote:last:* — keeps the streamer pure and the DB writes
    + delivery in the worker. A Redis lock keeps runs strictly serial: a backlog
    of queued evaluations must not double-fire the same crossing."""
    from app.alerts.evaluate import evaluate_alerts
    from app.core.db import session_scope
    from app.core.locks import redis_lock
    from app.providers.registry import _shared_redis

    with redis_lock(_shared_redis(), "alerts:evaluate", ttl_seconds=110) as acquired:
        if not acquired:
            log.info("evaluate_price_alerts skipped: another run holds the lock")
            return {"skipped": "locked"}
        with session_scope() as session:
            return evaluate_alerts(session)


@celery_app.task(name="worker.tasks.sync_exchange", time_limit=600, soft_time_limit=570)
def sync_exchange(account_id: int, repair: bool = False) -> int:
    """Read-only Bitso sync for one exchange account; returns exchange_sync_runs.id.
    repair=True (resync flow) overwrites synced trade rows from fresh provider data.
    A per-account lock keeps "Sync now" and the nightly beat from interleaving —
    two live BitsoClients would break per-instance nonce monotonicity."""
    from app.core.db import SessionLocal
    from app.core.locks import redis_lock
    from app.exchanges.sync import sync_bitso_account
    from app.providers.registry import _shared_redis

    with redis_lock(
        _shared_redis(), f"exchange:sync:{account_id}", ttl_seconds=590
    ) as acquired:
        if not acquired:
            log.info("sync_exchange skipped: sync already running", account_id=account_id)
            return -1
        return sync_bitso_account(SessionLocal, account_id, repair=repair)


@celery_app.task(name="worker.tasks.sync_exchange_nightly")
def sync_exchange_nightly() -> list[int]:
    """Nightly beat: sync every bitso-linked exchange account (skips if no keys)."""
    from app.exchanges.sync import sync_all_bitso_accounts

    return sync_all_bitso_accounts()


@celery_app.task(name="worker.tasks.send_morning_brief", time_limit=300, soft_time_limit=270)
def send_morning_brief() -> dict:
    """08:45 beat: ONE consolidated notification — portfolio snapshot, new
    candidates, overnight memos, upcoming maturities, alert recap, system line.
    Locked + once-per-local-day inside, so retries and catch-ups can't double-send."""
    from app.briefing.morning import send_morning_brief as run_brief
    from app.core.db import session_scope
    from app.core.locks import redis_lock
    from app.providers.registry import _shared_redis

    with redis_lock(_shared_redis(), "brief:morning", ttl_seconds=290) as acquired:
        if not acquired:
            log.info("morning_brief skipped: another run holds the lock")
            return {"status": "locked"}
        with session_scope() as session:
            return run_brief(session)


@celery_app.task(name="worker.tasks.run_ops_watchdog", time_limit=120, soft_time_limit=100)
def run_ops_watchdog() -> dict:
    """Hourly beat: notify (deduped per issue per day) when ingestion goes red,
    the quote streamer stops heartbeating, or backups go stale."""
    from app.core.db import session_scope
    from app.health.watchdog import run_watchdog

    with session_scope() as session:
        return run_watchdog(session)
