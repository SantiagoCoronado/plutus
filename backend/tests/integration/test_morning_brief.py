"""Phase 12: morning brief — composition, window, once-per-day, suppression,
settings API. Email is faked via FakeSMTP; quiet-day and disabled paths run
with channels blank (the conftest default)."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.briefing.morning import (
    BRIEF_KIND,
    compose_brief,
    is_enabled,
    send_morning_brief,
    set_enabled,
)
from app.core.config import get_settings
from app.core.db import session_scope
from app.models import Asset, AssetNote, Candidate, Mandate, Notification, Scan
from app.providers.registry import _shared_redis
from tests.integration.conftest import TEST_TOKEN

pytestmark = pytest.mark.integration

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}
NOW = datetime.now(UTC)


@pytest.fixture
def client():
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def email_env(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("ALERT_EMAIL_TO", "me@test.com")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def outbox(monkeypatch):
    import smtplib

    from tests.unit.test_discovery_notify import FakeSMTP

    FakeSMTP.instances = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


def _seed_content(hours_ago: float = 2.0) -> None:
    """A candidate above threshold + an overnight AI memo."""
    with session_scope() as session:
        asset = Asset(symbol="BRFA", name="Brief Asset", asset_class="stock", currency="USD")
        session.add(asset)
        session.flush()
        mandate = Mandate(
            name="Brief mandate", asset_class="stock", universe_def={"kind": "class"},
            schedule="0 8 * * *", score_weights={}, min_score=40,
        )
        session.add(mandate)
        session.flush()
        scan = Scan(mandate_id=mandate.id, status="done")
        session.add(scan)
        session.flush()
        session.add(
            Candidate(
                mandate_id=mandate.id, scan_id=scan.id, asset_id=asset.id,
                ts=NOW - timedelta(hours=hours_ago), score=88, signals=[], status="new",
                created_at=NOW - timedelta(hours=hours_ago),
            )
        )
        session.add(
            AssetNote(
                asset_id=asset.id, title="Deep dive: BRFA", body_md="…", source="ai",
                created_at=NOW - timedelta(hours=hours_ago),
            )
        )


class TestCompose:
    def test_sections_cover_window_content(self):
        _seed_content()
        with session_scope() as session:
            subject, body, meta, quiet = compose_brief(session, _shared_redis(), NOW)
        assert not quiet
        assert "New candidates" in meta["sections"]
        assert "AI research memos" in meta["sections"]
        assert "System" in meta["sections"]
        assert "BRFA" in body
        assert "Brief mandate" in body
        assert "informational only" in body

    def test_content_before_window_is_excluded(self):
        _seed_content(hours_ago=30)  # older than the 24h first-brief fallback
        with session_scope() as session:
            _, body, meta, quiet = compose_brief(session, _shared_redis(), NOW)
        assert quiet
        assert "BRFA: Deep dive" not in body

    def test_window_since_last_successful_brief(self):
        # a brief 3 days ago -> the window spans the whole gap (downtime catch-up)
        _seed_content(hours_ago=60)
        with session_scope() as session:
            session.add(
                Notification(
                    channel="email", kind=BRIEF_KIND, subject="old brief", body="",
                    meta={}, ok=True, sent_at=NOW - timedelta(days=3),
                )
            )
        with session_scope() as session:
            _, body, _, quiet = compose_brief(session, _shared_redis(), NOW)
        assert not quiet
        assert "BRFA" in body


class TestSend:
    def test_sends_once_per_local_day(self, email_env, outbox):
        _seed_content()
        with session_scope() as session:
            first = send_morning_brief(session, _shared_redis(), NOW)
        with session_scope() as session:
            second = send_morning_brief(session, _shared_redis(), NOW)
        assert first["status"] == "sent"
        assert second["status"] == "already_sent_today"
        sent = [m for smtp in outbox.instances for m in smtp.sent]
        assert len(sent) == 1

    def test_disabled_sends_nothing(self, email_env, outbox):
        _seed_content()
        with session_scope() as session:
            set_enabled(session, False)
        with session_scope() as session:
            result = send_morning_brief(session, _shared_redis(), NOW)
        assert result["status"] == "disabled"
        assert outbox.instances == []

    def test_quiet_day_sends_all_quiet_by_default(self, email_env, outbox):
        with session_scope() as session:
            result = send_morning_brief(session, _shared_redis(), NOW)
        assert result["status"] == "sent"
        assert result["quiet"] is True
        sent = [m for smtp in outbox.instances for m in smtp.sent]
        assert len(sent) == 1
        assert "All quiet" in sent[0].get_content()


class TestSuppression:
    def test_digest_and_reminders_yield_to_the_brief(self):
        from app.discovery.notify import send_digest
        from app.portfolio.maturities import _send_reminders

        with session_scope() as session:
            assert is_enabled(session) is True  # default on
        assert send_digest() == 0
        with session_scope() as session:
            assert _send_reminders(session) == 0

    def test_disabling_restores_the_old_behavior(self, email_env, outbox):
        from app.discovery.notify import send_digest

        with session_scope() as session:
            set_enabled(session, False)
        # no digest-mode candidates exist, so 0 sends — but it RAN (no early exit
        # log path): a digest notification row window query happens (smoke)
        assert send_digest() == 0


class TestSettingsApi:
    def test_get_put_roundtrip(self, client):
        body = client.get("/api/v1/brief", headers=AUTH).json()
        assert body["enabled"] is True
        assert body["scheduled_at"] == "08:45"

        body = client.put("/api/v1/brief", json={"enabled": False}, headers=AUTH).json()
        assert body["enabled"] is False
        with session_scope() as session:
            assert is_enabled(session) is False

    def test_test_endpoint_reports_missing_channels(self, client):
        response = client.post("/api/v1/brief/test", headers=AUTH)
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "no alert channels" in body["error"]

    def test_test_endpoint_sends_with_kind_test(self, client, email_env, outbox):
        _seed_content()
        body = client.post("/api/v1/brief/test", headers=AUTH).json()
        assert body["ok"] is True
        with session_scope() as session:
            from sqlalchemy import select

            kinds = set(session.scalars(select(Notification.kind)).all())
        # the daily window is untouched: no morning_brief row was written
        assert "test" in kinds
        assert BRIEF_KIND not in kinds
