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
}
