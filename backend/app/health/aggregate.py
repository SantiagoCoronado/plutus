"""Ingestion health aggregate: staleness per beat job + provider budget usage.

Health is judged on the age of the last SUCCESSFUL run against each job's
expected beat cadence — a failed run that self-heals on the next schedule never
turns the light, while a job that silently stops running does. Budget usage is
read from the day/month counters RateLimitedClient increments in Redis.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import IngestionRun
from app.providers.base import PROVIDER_LIMITS

# (amber after, red after) — thresholds leave slack for one missed schedule slot
# before amber and roughly two before red.
DAILY = (timedelta(hours=30), timedelta(hours=54))
WEEKLY = (timedelta(days=8), timedelta(days=15))
QUARTER_HOURLY = (timedelta(hours=2), timedelta(hours=12))

# Beat-scheduled jobs (worker/celery_app.py) keyed by the job_name each writes
# to ingestion_runs. Ad-hoc jobs (backfill, universe_backfill, ...) have no
# cadence and never affect the overall light.
EXPECTED_CADENCE: dict[str, tuple[timedelta, timedelta]] = {
    "eod_crypto": DAILY,
    "eod_forex": DAILY,
    "eod_stock": DAILY,
    "metrics_refresh": DAILY,
    "fundamentals_refresh": WEEKLY,
    "news_pull": QUARTER_HOURLY,
}

LOOKBACK = timedelta(days=14)


def staleness_verdict(
    now: datetime,
    cadence: tuple[timedelta, timedelta],
    last_success_at: datetime | None,
) -> str:
    """Pure green/amber/red call for one job — no success on record is amber
    (worth a look, but not the hard red of a known-broken pipeline)."""
    if last_success_at is None:
        return "amber"
    amber_after, red_after = cadence
    age = now - last_success_at
    if age > red_after:
        return "red"
    if age > amber_after:
        return "amber"
    return "green"


def overall_status(lights: list[str]) -> str:
    if "red" in lights:
        return "red"
    if "amber" in lights:
        return "amber"
    return "green"


def summarize_jobs(runs: list[dict], now: datetime) -> list[dict]:
    """Fold a newest-first run list into one health row per job_name.

    Each run dict carries job_name, provider, asset_class, status, started_at,
    finished_at, rows_written, symbols_ok, symbols_failed. Expected jobs come
    first (schedule order), then any ad-hoc jobs seen in the window.
    """
    seen: dict[str, dict] = {}
    for run in runs:
        job = seen.setdefault(
            run["job_name"],
            {"last": run, "last_success_at": None, "provider": None, "asset_class": None},
        )
        # newest non-null wins — a failed run may record provider=None
        if job["provider"] is None:
            job["provider"] = run["provider"]
        if job["asset_class"] is None:
            job["asset_class"] = run["asset_class"]
        if run["status"] == "success" and job["last_success_at"] is None:
            job["last_success_at"] = run["finished_at"] or run["started_at"]

    ordered = list(EXPECTED_CADENCE) + sorted(set(seen) - set(EXPECTED_CADENCE))
    return [_job_entry(job_name, seen.get(job_name), now) for job_name in ordered]


def _job_entry(job_name: str, job: dict | None, now: datetime) -> dict:
    cadence = EXPECTED_CADENCE.get(job_name)
    if job is None:
        return {
            "job_name": job_name,
            "provider": None,
            "asset_class": None,
            "last_status": None,
            "last_run_at": None,
            "last_success_at": None,
            "staleness": "amber",
            "rows_written": None,
            "symbols_ok": None,
            "symbols_failed": None,
            "note": "never ran",
        }
    last = job["last"]
    staleness = (
        staleness_verdict(now, cadence, job["last_success_at"]) if cadence else "green"
    )
    return {
        "job_name": job_name,
        "provider": job["provider"],
        "asset_class": job["asset_class"],
        "last_status": last["status"],
        "last_run_at": last["finished_at"] or last["started_at"],
        "last_success_at": job["last_success_at"],
        "staleness": staleness,
        "rows_written": last["rows_written"],
        "symbols_ok": last["symbols_ok"],
        "symbols_failed": last["symbols_failed"],
        "note": None,
    }


def provider_budgets(redis_client, now: datetime | None = None) -> list[dict]:
    """Current usage against every hard day/month budget in PROVIDER_LIMITS.

    Reads the counters RateLimitedClient._check_budget increments
    (budget:{provider}:day:YYYYMMDD / budget:{provider}:month:YYYYMM, UTC).
    A missing key simply means no calls yet today/this month.
    """
    now = now or datetime.now(UTC)
    budgets = []
    for provider, limits in sorted(PROVIDER_LIMITS.items()):
        windows = (
            ("day", limits.day_budget, f"budget:{provider}:day:{now:%Y%m%d}"),
            ("month", limits.month_budget, f"budget:{provider}:month:{now:%Y%m}"),
        )
        for window, budget, key in windows:
            if budget is None:
                continue
            raw = redis_client.get(key)
            used = int(raw) if raw is not None else 0
            budgets.append(
                {
                    "provider": provider,
                    "window": window,
                    "used": used,
                    "budget": budget,
                    "pct": round(used / budget * 100, 1),
                }
            )
    return budgets


def ingestion_health(session: Session, redis_client=None) -> dict:
    """One payload for the Settings section and the dashboard footer dot."""
    if redis_client is None:
        from app.providers.registry import _shared_redis

        redis_client = _shared_redis()

    now = datetime.now(UTC)
    rows = session.scalars(
        select(IngestionRun)
        .where(IngestionRun.started_at >= now - LOOKBACK)
        .order_by(IngestionRun.started_at.desc(), IngestionRun.id.desc())
    ).all()
    runs = [
        {
            "job_name": row.job_name,
            "provider": row.provider,
            "asset_class": row.asset_class,
            "status": row.status,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "rows_written": row.rows_written,
            "symbols_ok": row.symbols_ok,
            "symbols_failed": row.symbols_failed,
        }
        for row in rows
    ]
    jobs = summarize_jobs(runs, now)
    return {
        "status": overall_status([job["staleness"] for job in jobs]),
        "jobs": jobs,
        "budgets": provider_budgets(redis_client, now),
    }
