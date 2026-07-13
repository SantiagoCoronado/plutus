"""Phase 10 M3: ops watchdog — issue detection + once-per-day notification dedup.

Channels are pinned blank by the integration conftest, so deliver() records
nothing outbound; we assert on issue codes and dedup counters, not on emails.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.db import session_scope
from app.health.watchdog import BACKUP_HEARTBEAT_KEY, find_issues, run_watchdog
from app.models import AppSetting
from app.providers.registry import _shared_redis
from app.quotes.streamer import HEARTBEAT_KEY

pytestmark = pytest.mark.integration


def _codes(session, redis) -> list[str]:
    return [code for code, _ in find_issues(session, redis)]


def _fresh_heartbeats(redis) -> None:
    redis.setex(HEARTBEAT_KEY, 120, "1")
    with session_scope() as session:
        session.add(
            AppSetting(
                key=BACKUP_HEARTBEAT_KEY,
                value=datetime.now(UTC).isoformat(),
                is_secret=False,
            )
        )


class TestFindIssues:
    def test_missing_heartbeats_are_flagged(self):
        redis = _shared_redis()
        with session_scope() as session:
            codes = _codes(session, redis)
        assert "quotes_silent" in codes
        assert "backup_missing" in codes
        # no ingestion runs at all reads as green (fresh install), not red
        assert "ingestion_stale" not in codes

    def test_fresh_heartbeats_are_quiet(self):
        redis = _shared_redis()
        _fresh_heartbeats(redis)
        with session_scope() as session:
            codes = _codes(session, redis)
        assert codes == []

    def test_stale_backup_is_flagged_beyond_26h(self):
        redis = _shared_redis()
        redis.setex(HEARTBEAT_KEY, 120, "1")
        with session_scope() as session:
            session.add(
                AppSetting(
                    key=BACKUP_HEARTBEAT_KEY,
                    value=(datetime.now(UTC) - timedelta(hours=30)).isoformat(),
                    is_secret=False,
                )
            )
        with session_scope() as session:
            codes = _codes(session, redis)
        assert codes == ["backup_stale"]

    def test_unparseable_heartbeat_reads_as_stale(self):
        redis = _shared_redis()
        redis.setex(HEARTBEAT_KEY, 120, "1")
        with session_scope() as session:
            session.add(
                AppSetting(key=BACKUP_HEARTBEAT_KEY, value="not-a-date", is_secret=False)
            )
        with session_scope() as session:
            codes = _codes(session, redis)
        assert codes == ["backup_stale"]


class TestDedup:
    def test_each_issue_notifies_once_per_day(self):
        redis = _shared_redis()
        with session_scope() as session:
            first = run_watchdog(session, redis)
        with session_scope() as session:
            second = run_watchdog(session, redis)
        assert first["issues"] >= 2
        assert first["notified"] == first["issues"]
        assert second["issues"] == first["issues"]  # still broken...
        assert second["notified"] == 0  # ...but no re-notification today

    def test_watchdog_task_runs_via_worker(self):
        from worker.tasks import run_ops_watchdog

        summary = run_ops_watchdog()
        assert set(summary) == {"issues", "notified"}
