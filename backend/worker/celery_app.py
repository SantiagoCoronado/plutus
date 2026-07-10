from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "plutus",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["worker.tasks"],
)

celery_app.conf.update(
    timezone=settings.tz,
    enable_utc=True,
    task_acks_late=True,
    task_time_limit=1800,
    task_soft_time_limit=1500,
    result_expires=86400,
    worker_concurrency=2,
    broker_connection_retry_on_startup=True,
)

# Staggered per provider budgets; hours are in the configured local TZ
# (03:00 America/Mexico_City ≈ 09:00 UTC — previous UTC day complete, US market long closed)
celery_app.conf.beat_schedule = {
    "eod-crypto": {
        "task": "worker.tasks.ingest_eod",
        "schedule": crontab(hour=3, minute=0),
        "args": ("crypto",),
    },
    "eod-forex": {
        "task": "worker.tasks.ingest_eod",
        "schedule": crontab(hour=3, minute=10),
        "args": ("forex",),
    },
    "eod-stocks": {
        "task": "worker.tasks.ingest_eod",
        "schedule": crontab(hour=3, minute=20),
        "args": ("stock",),
    },
    # after all EOD jobs — snapshots read the bars written above. The ~100-stock
    # universe paces at Tiingo's bucket (~80s/symbol), so eod-stocks finishes ~05:40.
    "metrics-refresh": {
        "task": "worker.tasks.refresh_metrics",
        "schedule": crontab(hour=6, minute=30),
    },
    "fundamentals-refresh": {
        "task": "worker.tasks.refresh_fundamentals",
        "schedule": crontab(hour=6, minute=30, day_of_week="sun"),
    },
    # expires ≤ cadence on the frequent entries: a worker backlog drops stale
    # duplicate ticks instead of replaying them all at once
    "news-pull": {
        "task": "worker.tasks.pull_news",
        "schedule": crontab(minute="*/15"),
        "options": {"expires": 14 * 60},
    },
    # checks every active mandate's cron and enqueues due scans
    "discovery-dispatcher": {
        "task": "worker.tasks.dispatch_scans",
        "schedule": crontab(minute="*/5"),
        "options": {"expires": 4 * 60},
    },
    # after the 06:30 metrics refresh and the default 07:30 mandate preset,
    # so the daily summary covers the same morning's scans
    "alert-digest": {
        "task": "worker.tasks.send_alert_digest",
        "schedule": crontab(hour=8, minute=0),
    },
    # bank-investment maintenance: roll auto-renewals, remind before maturities
    "maturity-check": {
        "task": "worker.tasks.check_maturities",
        "schedule": crontab(hour=8, minute=30),
        "options": {"expires": 6 * 3600},
    },
    # AI research memos for last night's top candidates — after the 06:30 metrics
    # refresh and 07:30 preset scans so memos cover the same morning's inbox
    "agent-research-memos": {
        "task": "worker.tasks.run_nightly_research_memos",
        "schedule": crontab(hour=8, minute=15),
    },
    # read-only Bitso pull, after the EOD ingestion jobs (03:00–03:20)
    "sync-bitso": {
        "task": "worker.tasks.sync_exchange_nightly",
        "schedule": crontab(hour=3, minute=40),
        "options": {"expires": 6 * 3600},
    },
    # fire armed price alerts on a threshold crossing — reads the live quote cache.
    # expires < the minute cadence: a worker backlog drops stale duplicates
    # instead of replaying them (the Redis lock is the second line of defense)
    "evaluate-price-alerts": {
        "task": "worker.tasks.evaluate_price_alerts",
        "schedule": crontab(minute="*"),
        "options": {"expires": 55},
    },
}
