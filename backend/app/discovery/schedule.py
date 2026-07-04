"""Mandate scheduling on celery's own cron implementation (no extra dependency).

A mandate's schedule is a standard 5-field cron string evaluated in the app
timezone (settings.tz) — the same semantics as the beat entries. Binding the
crontab to our celery app explicitly means the API process and the worker
compute identical due/next times.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from celery.schedules import crontab

CRON_FIELDS = ("minute", "hour", "day-of-month", "month", "day-of-week")


def parse_cron(expr: str, nowfun: Callable[[], datetime] | None = None) -> crontab:
    """Validate + build a celery crontab from a 5-field cron string.

    Raises ValueError with a plain-English message on any invalid input.
    """
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(
            "schedule must be 5 cron fields: minute hour day-of-month month day-of-week"
        )
    from worker.celery_app import celery_app

    # celery's crontab matches fields against the wall clock of the datetimes it
    # receives (it only localizes naive ones) — so "now" must be app-tz aware
    wrapped = None
    if nowfun is not None:
        tz = celery_app.timezone
        wrapped = lambda: nowfun().astimezone(tz)  # noqa: E731
    try:
        return crontab(
            minute=fields[0],
            hour=fields[1],
            day_of_month=fields[2],
            month_of_year=fields[3],
            day_of_week=fields[4],
            app=celery_app,
            nowfun=wrapped,
        )
    except (ValueError, KeyError) as exc:
        raise ValueError(f"invalid schedule: {exc}") from exc


def is_due(
    expr: str, last_run_at: datetime, nowfun: Callable[[], datetime] | None = None
) -> bool:
    cron = parse_cron(expr, nowfun=nowfun)
    return bool(cron.is_due(last_run_at.astimezone(cron.tz)).is_due)


def next_run_at(
    expr: str, last_run_at: datetime, nowfun: Callable[[], datetime] | None = None
) -> datetime:
    """Next fire time in UTC. When overdue, "now": the dispatcher picks it up
    within its 5-minute cycle."""
    cron = parse_cron(expr, nowfun=nowfun)
    now = cron.now()
    eta = now + cron.remaining_estimate(last_run_at.astimezone(cron.tz))
    return max(now, eta).astimezone(UTC)
