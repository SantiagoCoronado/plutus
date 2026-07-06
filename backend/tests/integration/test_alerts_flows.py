"""Phase 7 M1 integration: price-alert CRUD, re-arm/edit crossing-edge semantics,
the notifications kind CHECK accepting 'price_alert', and the three agent alert
tools end-to-end through the executor chokepoint."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db import SessionLocal, session_scope
from app.ingestion.seed import seed_assets
from app.llm.executor import execute_tool
from app.models import AgentToolCall, AlertRule, Notification
from tests.integration.conftest import TEST_TOKEN

pytestmark = pytest.mark.integration

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture
def client():
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def seeded():
    return {symbol: asset_id for asset_id, symbol in seed_assets()}


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


def run(db, name, args, **kwargs):
    kwargs.setdefault("source", "app")
    return execute_tool(db, name, args, **kwargs)


class TestAlertsApi:
    def test_crud_happy_path(self, seeded, client):
        aapl = seeded["AAPL"]
        resp = client.post(
            "/api/v1/alerts",
            headers=AUTH,
            json={"asset_id": aapl, "condition": "above", "threshold": 200, "note": "breakout"},
        )
        assert resp.status_code == 201, resp.text
        rule = resp.json()
        assert rule["status"] == "armed"
        assert rule["symbol"] == "AAPL"
        assert rule["threshold"] == 200.0
        assert rule["note"] == "breakout"
        alert_id = rule["id"]

        listed = client.get(f"/api/v1/alerts?asset_id={aapl}", headers=AUTH).json()
        assert [r["id"] for r in listed] == [alert_id]
        assert listed[0]["symbol"] == "AAPL"

        patched = client.patch(
            f"/api/v1/alerts/{alert_id}", headers=AUTH, json={"status": "disabled"}
        )
        assert patched.status_code == 200
        assert patched.json()["status"] == "disabled"

        armed = client.get("/api/v1/alerts?status=armed", headers=AUTH).json()
        assert alert_id not in [r["id"] for r in armed]

        assert client.delete(f"/api/v1/alerts/{alert_id}", headers=AUTH).status_code == 204
        assert client.get("/api/v1/alerts", headers=AUTH).json() == []

    def test_create_unknown_asset_404(self, seeded, client):
        resp = client.post(
            "/api/v1/alerts",
            headers=AUTH,
            json={"asset_id": 999999, "condition": "above", "threshold": 100},
        )
        assert resp.status_code == 404

    def test_create_invalid_condition_422(self, seeded, client):
        resp = client.post(
            "/api/v1/alerts",
            headers=AUTH,
            json={"asset_id": seeded["AAPL"], "condition": "sideways", "threshold": 100},
        )
        assert resp.status_code == 422

    def test_create_invalid_threshold_422(self, seeded, client):
        resp = client.post(
            "/api/v1/alerts",
            headers=AUTH,
            json={"asset_id": seeded["AAPL"], "condition": "above", "threshold": 0},
        )
        assert resp.status_code == 422

    def test_patch_triggered_is_rejected_422(self, seeded, client):
        alert_id = client.post(
            "/api/v1/alerts",
            headers=AUTH,
            json={"asset_id": seeded["AAPL"], "condition": "above", "threshold": 100},
        ).json()["id"]
        resp = client.patch(
            f"/api/v1/alerts/{alert_id}", headers=AUTH, json={"status": "triggered"}
        )
        assert resp.status_code == 422

    def test_patch_missing_404(self, seeded, client):
        resp = client.patch("/api/v1/alerts/424242", headers=AUTH, json={"status": "armed"})
        assert resp.status_code == 404

    def test_rearm_resets_last_price_and_triggered_at(self, seeded, client):
        alert_id = client.post(
            "/api/v1/alerts",
            headers=AUTH,
            json={"asset_id": seeded["AAPL"], "condition": "above", "threshold": 100},
        ).json()["id"]
        # simulate the evaluator having fired
        with session_scope() as session:
            rule = session.get(AlertRule, alert_id)
            rule.status = "triggered"
            rule.last_price = 150
            rule.last_triggered_at = datetime.now(UTC)

        patched = client.patch(
            f"/api/v1/alerts/{alert_id}", headers=AUTH, json={"status": "armed"}
        ).json()
        assert patched["status"] == "armed"
        assert patched["last_price"] is None
        assert patched["last_triggered_at"] is None

    def test_edit_threshold_on_armed_rule_resets_last_price(self, seeded, client):
        alert_id = client.post(
            "/api/v1/alerts",
            headers=AUTH,
            json={"asset_id": seeded["AAPL"], "condition": "above", "threshold": 100},
        ).json()["id"]
        with session_scope() as session:
            session.get(AlertRule, alert_id).last_price = 90

        patched = client.patch(
            f"/api/v1/alerts/{alert_id}", headers=AUTH, json={"threshold": 120}
        ).json()
        assert patched["threshold"] == 120.0
        assert patched["last_price"] is None


class TestNotificationsKind:
    def test_price_alert_kind_accepted(self, seeded):
        with session_scope() as session:
            session.add(
                Notification(
                    channel="email",
                    kind="price_alert",
                    subject="AAPL crossed above 200",
                    body="AAPL is now 201.0",
                    ok=True,
                )
            )
        with session_scope() as session:
            row = session.scalar(
                select(Notification).where(Notification.kind == "price_alert")
            )
            assert row is not None and row.subject == "AAPL crossed above 200"


class TestAlertAgentTools:
    def test_create_list_delete_flow(self, seeded, db):
        created = run(
            db,
            "create_alert_rule",
            {"symbol": "AAPL", "condition": "below", "threshold": 120, "note": "dip"},
        )
        assert created.ok, created.error
        alert_id = created.result["alert_id"]
        assert created.result["condition"] == "below"
        assert db.get(AlertRule, alert_id) is not None

        db.expire_all()
        audit = db.scalars(select(AgentToolCall).order_by(AgentToolCall.id.desc())).first()
        assert audit.name == "create_alert_rule"
        assert audit.status == "done" and audit.tier == "write"

        listed = run(db, "list_alert_rules", {"symbol": "AAPL"})
        assert listed.ok
        assert alert_id in [a["id"] for a in listed.result["alerts"]]

        deleted = run(db, "delete_alert_rule", {"alert_id": alert_id})
        assert deleted.ok
        db.expire_all()
        assert db.get(AlertRule, alert_id) is None

    def test_unknown_symbol_is_error_result(self, seeded, db):
        outcome = run(
            db, "create_alert_rule", {"symbol": "ZZZQ", "condition": "above", "threshold": 100}
        )
        assert not outcome.ok
        assert "not a tracked asset" in outcome.error
