"""Hourly ops watchdog (spec phase 10 M3): push, don't wait to be looked at.

The Settings page and dashboard footer already *show* ingestion health — but a
dead beat, a wedged streamer, or a silently failing backup only surfaces when
someone happens to look. This task turns the same signals into email/Telegram
notifications through the existing alert channels:

  - ingestion rollup red (any job stale beyond its red threshold)
  - quotes streamer heartbeat missing (quotes:heartbeat, TTL 120s)
  - last successful pg_dump older than BACKUP_MAX_AGE_H (heartbeat row written
    into app_settings by scripts/backup.sh), or no backup recorded at all

Each issue notifies at most once per local day (Redis SET NX dedup), so the
hourly cadence can't storm a broken channel.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.discovery.notify import deliver
from app.health.aggregate import ingestion_health
from app.models import AppSetting
from app.quotes.streamer import HEARTBEAT_KEY

log = get_logger(__name__)

# app_settings row upserted by scripts/backup.sh after every successful dump
BACKUP_HEARTBEAT_KEY = "backup_last_success_at"
BACKUP_MAX_AGE_H = 26  # nightly cadence + slack
DEDUP_TTL_S = 24 * 3600


def find_issues(
    session: Session, redis_client, now: datetime | None = None
) -> list[tuple[str, str]]:
    """(code, human message) per current problem — pure enough to unit test."""
    now = now or datetime.now(UTC)
    issues: list[tuple[str, str]] = []

    health = ingestion_health(session, redis_client)
    if health["status"] == "red":
        stale = [job["job_name"] for job in health["jobs"] if job["staleness"] == "red"]
        issues.append(
            (
                "ingestion_stale",
                "Ingestion is red: "
                + ", ".join(stale)
                + " — check the worker/beat containers and provider budgets in Settings.",
            )
        )

    if not redis_client.get(HEARTBEAT_KEY):
        issues.append(
            (
                "quotes_silent",
                "The live-quote streamer has not heartbeated for over 2 minutes — "
                "live prices, watchlists, and price alerts are running blind. "
                "Check the quotes container.",
            )
        )

    row = session.get(AppSetting, BACKUP_HEARTBEAT_KEY)
    if row is None:
        issues.append(
            (
                "backup_missing",
                "No successful database backup has ever been recorded. The backup "
                "container dumps nightly at 04:00 local — check `make backup-list`.",
            )
        )
    else:
        try:
            last = datetime.fromisoformat(row.value)
        except ValueError:
            last = None
        if last is None or now - last > timedelta(hours=BACKUP_MAX_AGE_H):
            age = f"{(now - last).total_seconds() / 3600:.0f}h ago" if last else "unparseable"
            issues.append(
                (
                    "backup_stale",
                    f"Last successful database backup: {age} (threshold "
                    f"{BACKUP_MAX_AGE_H}h). Check the backup container logs.",
                )
            )

    return issues


def run_watchdog(session: Session, redis_client=None, now: datetime | None = None) -> dict:
    if redis_client is None:
        from app.providers.registry import _shared_redis

        redis_client = _shared_redis()
    now = now or datetime.now(UTC)

    issues = find_issues(session, redis_client, now)
    notified = 0
    for code, message in issues:
        dedup_key = f"watchdog:sent:{code}:{now:%Y%m%d}"
        if not redis_client.set(dedup_key, "1", nx=True, ex=DEDUP_TTL_S):
            continue  # already notified about this issue today
        try:
            subject = f"Watchdog: {code.replace('_', ' ')}"
            deliver(session, "watchdog", subject, message, {"code": code})
            notified += 1
        except Exception as exc:  # noqa: BLE001 — a broken channel must not fail the tick
            log.warning("watchdog_deliver_failed", code=code, error=str(exc))

    log.info("watchdog_ran", issues=len(issues), notified=notified)
    return {"issues": len(issues), "notified": notified}
