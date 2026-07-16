"""Phase 4 integration: mandate scans end-to-end, dedup, dispatcher, and the API."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.analysis.metrics import _upsert_metrics
from app.core.db import session_scope
from app.discovery.runner import dispatch_due_mandates, execute_scan
from app.ingestion.eod import upsert_candles
from app.ingestion.seed import seed_assets
from app.models import Candidate, Mandate, Scan
from tests.integration.conftest import TEST_TOKEN

pytestmark = pytest.mark.integration

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}
N_BARS = 400

VALID_MANDATE = {
    "name": "Oversold large caps",
    "asset_class": "stock",
    "universe_def": {"type": "class"},
    "rules": {"field": "rsi_14", "op": "<", "value": 45},
    "schedule": "30 7 * * 1-5",
    "score_weights": {"rsi_extreme": 2.0, "mean_reversion": 1.0},
}


@pytest.fixture
def client():
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def seed_bars(session, asset_id: int, closes: np.ndarray, with_volume: bool = True) -> None:
    end = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    volumes = np.where(np.arange(len(closes)) % 2 == 0, 1e6, 1.1e6)
    rows = [
        {
            "asset_id": asset_id,
            "interval": "1d",
            "ts": end - timedelta(days=len(closes) - i),
            "open": closes[i] - 0.5,
            "high": closes[i] + 2.0,
            "low": closes[i] - 2.5,
            "close": closes[i],
            "volume": volumes[i] if with_volume else None,
        }
        for i in range(len(closes))
    ]
    upsert_candles(session, rows)


def oversold_closes() -> np.ndarray:
    """Mild rise, then a steep late slide: rsi_extreme + mean_reversion both trigger."""
    closes = np.linspace(100, 140, N_BARS)
    closes[-15:] = closes[-16] * (1 - 0.03) ** np.arange(1, 16)
    return closes


@pytest.fixture
def oversold_mandate():
    """AAPL (the only pure stock in the seed set) with bars engineered to trigger."""
    assets = dict((symbol, asset_id) for asset_id, symbol in seed_assets())
    with session_scope() as session:
        seed_bars(session, assets["AAPL"], oversold_closes())
        _upsert_metrics(
            session,
            assets["AAPL"],
            {"as_of": datetime.now(UTC).date(), "close": 89.0, "rsi_14": 12.0, "pe": 21.5},
        )
        mandate = Mandate(
            name="Oversold stocks",
            asset_class="stock",
            universe_def={"type": "class"},
            rules=None,
            schedule="30 7 * * 1-5",
            score_weights={"rsi_extreme": 1.0, "mean_reversion": 1.0, "breakout": 1.0},
            min_score=40.0,
            notify="off",
        )
        session.add(mandate)
        session.flush()
        mandate_id, asset_id = mandate.id, assets["AAPL"]
    return {"mandate_id": mandate_id, "asset_id": asset_id}


def new_scan(mandate_id: int) -> int:
    with session_scope() as session:
        scan = Scan(mandate_id=mandate_id)
        session.add(scan)
        session.flush()
        return scan.id


class TestScanFlow:
    def test_scan_produces_scored_candidate_with_context(self, oversold_mandate):
        scan_id = new_scan(oversold_mandate["mandate_id"])
        execute_scan(scan_id)

        with session_scope() as session:
            scan = session.get(Scan, scan_id)
            assert scan.status == "done", scan.error
            assert scan.started_at is not None and scan.finished_at is not None
            assert scan.stats["universe"] == 1
            assert scan.stats["analyzed"] == 1
            assert scan.stats["created"] == 1
            assert scan.stats["duration_ms"] >= 0

            candidate = session.query(Candidate).one()
            assert candidate.status == "new"
            assert candidate.scan_id == scan_id
            assert candidate.asset_id == oversold_mandate["asset_id"]
            assert 40 <= candidate.score <= 100

            by_key = {item["key"]: item for item in candidate.signals}
            assert by_key["rsi_extreme"]["triggered"]
            assert by_key["mean_reversion"]["triggered"]
            assert all("label" in item and "weight" in item for item in candidate.signals)

            context = candidate.context
            assert context["snapshot"]["pe"] == 21.5
            assert len(context["chart"]) == 120
            assert "rsi_extreme" in context["history_check"]

    def test_rerun_is_deduplicated_until_cooldown_expires(self, oversold_mandate):
        mandate_id = oversold_mandate["mandate_id"]
        execute_scan(new_scan(mandate_id))

        # unreviewed candidate -> the rerun creates nothing
        rerun_id = new_scan(mandate_id)
        execute_scan(rerun_id)
        with session_scope() as session:
            rerun = session.get(Scan, rerun_id)
            assert rerun.stats["created"] == 0
            assert rerun.stats["skipped_recent"] == 1
            assert session.query(Candidate).count() == 1

        # dismissed + outside the cooldown -> nominated again
        with session_scope() as session:
            candidate = session.query(Candidate).one()
            candidate.status = "dismissed"
            candidate.created_at = datetime.now(UTC) - timedelta(days=30)
        third_id = new_scan(mandate_id)
        execute_scan(third_id)
        with session_scope() as session:
            assert session.get(Scan, third_id).stats["created"] == 1
            assert session.query(Candidate).count() == 2

    def test_scan_with_no_matching_assets_is_done_and_empty(self):
        seed_assets()
        with session_scope() as session:
            mandate = Mandate(
                name="No crypto matches",
                asset_class="crypto",
                universe_def={"type": "class"},
                schedule="30 7 * * *",
                score_weights={"crypto_drawdown": 1.0},
                notify="off",
            )
            session.add(mandate)
            session.flush()
            mandate_id = mandate.id
        scan_id = new_scan(mandate_id)
        execute_scan(scan_id)
        with session_scope() as session:
            scan = session.get(Scan, scan_id)
            assert scan.status == "done", scan.error
            assert scan.stats["created"] == 0
            # BTC has no bars seeded -> counted as no-data, never a crash
            assert scan.stats["skipped_no_data"] == scan.stats["after_rules"]

    def test_failed_scan_records_error(self, oversold_mandate):
        with session_scope() as session:
            mandate = session.get(Mandate, oversold_mandate["mandate_id"])
            mandate.universe_def = {"type": "nonsense"}
        scan_id = new_scan(oversold_mandate["mandate_id"])
        execute_scan(scan_id)
        with session_scope() as session:
            scan = session.get(Scan, scan_id)
            assert scan.status == "failed"
            assert "unknown universe type" in scan.error


class TestDispatcher:
    def make_mandate(self, name: str, last_run_at, schedule: str = "*/5 * * * *") -> int:
        with session_scope() as session:
            mandate = Mandate(
                name=name,
                asset_class="stock",
                universe_def={"type": "class"},
                schedule=schedule,
                score_weights={"rsi_extreme": 1.0},
                last_run_at=last_run_at,
                notify="off",
            )
            session.add(mandate)
            session.flush()
            return mandate.id

    def test_due_mandate_gets_a_scan_and_advances_last_run(self, monkeypatch):
        seed_assets()
        stale = datetime.now(UTC) - timedelta(hours=2)
        mandate_id = self.make_mandate("due", stale)
        enqueued = []
        import worker.tasks

        monkeypatch.setattr(
            worker.tasks.run_scan, "delay", lambda scan_id: enqueued.append(scan_id)
        )

        dispatched = dispatch_due_mandates()
        assert len(dispatched) == 1
        assert enqueued == dispatched
        with session_scope() as session:
            mandate = session.get(Mandate, mandate_id)
            assert mandate.last_run_at > stale
            scan = session.get(Scan, dispatched[0])
            assert scan.status == "queued"

        # a queued scan blocks re-dispatch even once due again
        with session_scope() as session:
            session.get(Mandate, mandate_id).last_run_at = stale
        assert dispatch_due_mandates() == []

    def test_not_due_and_inactive_mandates_are_untouched(self, monkeypatch):
        seed_assets()
        # anchor to the current 5-minute window: "now - 30s" straddles a cron
        # boundary once every ten runs, making the mandate genuinely due
        now = datetime.now(UTC)
        just_ran = now.replace(minute=now.minute - now.minute % 5, second=0, microsecond=0)
        self.make_mandate("fresh", just_ran)
        with session_scope() as session:
            inactive = Mandate(
                name="inactive",
                asset_class="stock",
                universe_def={"type": "class"},
                schedule="*/5 * * * *",
                score_weights={"rsi_extreme": 1.0},
                last_run_at=datetime.now(UTC) - timedelta(days=1),
                active=False,
                notify="off",
            )
            session.add(inactive)
        import worker.tasks

        monkeypatch.setattr(
            worker.tasks.run_scan, "delay", lambda scan_id: pytest.fail("should not enqueue")
        )
        assert dispatch_due_mandates() == []

    def test_enqueue_failure_becomes_a_failed_scan_row(self, monkeypatch):
        seed_assets()
        mandate_id = self.make_mandate("broker-down", datetime.now(UTC) - timedelta(hours=2))
        import worker.tasks

        def boom(scan_id):
            raise ConnectionError("broker unreachable")

        monkeypatch.setattr(worker.tasks.run_scan, "delay", boom)
        assert dispatch_due_mandates() == []
        with session_scope() as session:
            scan = session.query(Scan).one()
            assert scan.mandate_id == mandate_id
            assert scan.status == "failed"
            assert "could not enqueue" in scan.error
            # last_run_at advanced: the next cron fire retries, no 5-minute storm
            assert session.get(Mandate, mandate_id).last_run_at > datetime.now(UTC) - timedelta(
                minutes=5
            )


class TestMandateApi:
    def test_crud_lifecycle(self, client):
        created = client.post("/api/v1/mandates", json=VALID_MANDATE, headers=AUTH)
        assert created.status_code == 201, created.text
        body = created.json()
        mandate_id = body["id"]
        assert body["next_run_at"] is not None
        assert body["stats"] == {
            "candidates_total": 0,
            "new": 0,
            "starred": 0,
            "dismissed": 0,
            "hit_rate": None,
        }

        assert client.post("/api/v1/mandates", json=VALID_MANDATE, headers=AUTH).status_code == 409

        listed = client.get("/api/v1/mandates", headers=AUTH).json()
        assert [m["name"] for m in listed] == ["Oversold large caps"]

        updated = client.put(
            f"/api/v1/mandates/{mandate_id}",
            json={**VALID_MANDATE, "min_score": 55.0, "notify": "digest"},
            headers=AUTH,
        )
        assert updated.status_code == 200
        assert updated.json()["min_score"] == 55.0

        patched = client.patch(
            f"/api/v1/mandates/{mandate_id}", json={"active": False}, headers=AUTH
        )
        assert patched.status_code == 200
        assert patched.json()["active"] is False

        assert client.delete(f"/api/v1/mandates/{mandate_id}", headers=AUTH).status_code == 204
        assert client.get(f"/api/v1/mandates/{mandate_id}", headers=AUTH).status_code == 404

    @pytest.mark.parametrize(
        ("override", "expected_path"),
        [
            ({"schedule": "not a cron"}, "schedule"),
            ({"score_weights": {"bogus_signal": 1.0}}, "score_weights.bogus_signal"),
            ({"score_weights": {"crypto_drawdown": 1.0}}, "score_weights.crypto_drawdown"),
            ({"score_weights": {"rsi_extreme": 0.0}}, "score_weights"),
            ({"rules": {"field": "bogus", "op": ">", "value": 1}}, "rules"),
            (
                {"universe_def": {"type": "watchlist", "watchlist_id": 999}},
                "universe_def.watchlist_id",
            ),
        ],
    )
    def test_validation_renders_per_path_errors(self, client, override, expected_path):
        resp = client.post("/api/v1/mandates", json={**VALID_MANDATE, **override}, headers=AUTH)
        assert resp.status_code == 422, resp.text
        errors = resp.json()["detail"]["errors"]
        assert any(str(e.get("path", "")).startswith(expected_path) for e in errors), errors

    def test_signals_endpoint_describes_the_registry(self, client):
        resp = client.get("/api/v1/mandates/signals", headers=AUTH)
        assert resp.status_code == 200
        by_key = {s["key"]: s for s in resp.json()}
        assert by_key["volume_anomaly"]["needs_volume"] is True
        assert by_key["momentum_rank"]["supports_history_check"] is False
        assert "crypto" in by_key["crypto_drawdown"]["asset_classes"]

    def test_run_now_creates_pollable_scan(self, client, oversold_mandate, monkeypatch):
        import worker.tasks

        monkeypatch.setattr(worker.tasks.run_scan, "delay", lambda scan_id: None)
        mandate_id = oversold_mandate["mandate_id"]

        created = client.post(f"/api/v1/mandates/{mandate_id}/scan", headers=AUTH)
        assert created.status_code == 201
        scan_id = created.json()["id"]
        assert created.json()["status"] == "queued"

        # a second run-now while one is in flight is refused
        assert client.post(f"/api/v1/mandates/{mandate_id}/scan", headers=AUTH).status_code == 409

        execute_scan(scan_id)
        scans = client.get(f"/api/v1/mandates/{mandate_id}/scans", headers=AUTH).json()
        assert scans[0]["id"] == scan_id
        assert scans[0]["status"] == "done"
        assert scans[0]["stats"]["created"] == 1

        # manual runs never move the standing schedule
        mandate = client.get(f"/api/v1/mandates/{mandate_id}", headers=AUTH).json()
        assert mandate["last_run_at"] is None


class TestCandidateApi:
    @pytest.fixture
    def with_candidates(self, oversold_mandate):
        execute_scan(new_scan(oversold_mandate["mandate_id"]))
        return oversold_mandate

    def test_inbox_list_patch_and_summary(self, client, with_candidates):
        listed = client.get("/api/v1/candidates", headers=AUTH).json()
        assert len(listed) == 1
        candidate = listed[0]
        assert candidate["symbol"] == "AAPL"
        assert candidate["mandate_name"] == "Oversold stocks"
        assert candidate["status"] == "new"
        assert candidate["signals"][0]["triggered"] is True
        assert "chart" in candidate["context"]

        starred = client.patch(
            f"/api/v1/candidates/{candidate['id']}", json={"status": "starred"}, headers=AUTH
        )
        assert starred.status_code == 200
        assert starred.json()["status"] == "starred"

        assert client.get("/api/v1/candidates?status=new", headers=AUTH).json() == []
        assert (
            len(client.get("/api/v1/candidates?status=starred", headers=AUTH).json()) == 1
        )
        assert (
            client.get("/api/v1/candidates?asset_class=crypto", headers=AUTH).json() == []
        )

        summary = client.get("/api/v1/candidates/summary", headers=AUTH).json()
        assert summary["by_status"]["starred"] == 1
        assert summary["by_mandate"][0]["hit_rate"] == 1.0

        # the mandate list embeds the same hit-rate stats
        mandates = client.get("/api/v1/mandates", headers=AUTH).json()
        target = next(
            m for m in mandates if m["id"] == with_candidates["mandate_id"]
        )
        assert target["stats"]["starred"] == 1
        assert target["stats"]["hit_rate"] == 1.0
        assert target["last_scan"]["status"] == "done"

    def test_patch_unknown_candidate_404s(self, client):
        resp = client.patch("/api/v1/candidates/999", json={"status": "starred"}, headers=AUTH)
        assert resp.status_code == 404

    def test_bad_status_value_is_rejected(self, client, with_candidates):
        listed = client.get("/api/v1/candidates", headers=AUTH).json()
        resp = client.patch(
            f"/api/v1/candidates/{listed[0]['id']}", json={"status": "nonsense"}, headers=AUTH
        )
        assert resp.status_code == 422


def _brief_off() -> None:
    """Pin the LEGACY notification path: the phase-12 morning brief (enabled by
    default) suppresses the standalone digest these tests verify."""
    from app.briefing.morning import set_enabled
    from app.core.db import session_scope

    with session_scope() as session:
        set_enabled(session, False)


class TestAlerts:
    @pytest.fixture
    def email_env(self, monkeypatch):
        from app.core.config import get_settings

        monkeypatch.setenv("SMTP_HOST", "smtp.test")
        monkeypatch.setenv("ALERT_EMAIL_TO", "me@test.com")
        get_settings.cache_clear()
        yield
        get_settings.cache_clear()

    @pytest.fixture
    def outbox(self, monkeypatch):
        import smtplib

        from tests.unit.test_discovery_notify import FakeSMTP

        FakeSMTP.instances = []
        monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
        return FakeSMTP

    def test_instant_scan_alert_sends_one_email(self, oversold_mandate, email_env, outbox):
        from app.models import Notification

        with session_scope() as session:
            session.get(Mandate, oversold_mandate["mandate_id"]).notify = "instant"
        execute_scan(new_scan(oversold_mandate["mandate_id"]))

        assert len(outbox.instances) == 1
        message = outbox.instances[0].sent[0]
        assert "1 new idea" in message["Subject"]
        assert "Oversold stocks" in message["Subject"]
        assert "AAPL" in message.get_content()

        with session_scope() as session:
            note = session.query(Notification).one()
            assert note.kind == "instant" and note.ok is True and note.channel == "email"
            assert note.meta["candidate_ids"]

    def test_alert_threshold_suppresses_low_scores(self, oversold_mandate, email_env, outbox):
        with session_scope() as session:
            mandate = session.get(Mandate, oversold_mandate["mandate_id"])
            mandate.notify = "instant"
            mandate.notify_min_score = 99.5
        execute_scan(new_scan(oversold_mandate["mandate_id"]))
        assert outbox.instances == []

    def test_unconfigured_channels_never_fail_the_scan(self, oversold_mandate):
        from app.models import Notification

        with session_scope() as session:
            session.get(Mandate, oversold_mandate["mandate_id"]).notify = "instant"
        scan_id = new_scan(oversold_mandate["mandate_id"])
        execute_scan(scan_id)
        with session_scope() as session:
            assert session.get(Scan, scan_id).status == "done"
            assert session.query(Notification).count() == 0

    def seed_digest_candidates(self):
        assets = dict((symbol, asset_id) for asset_id, symbol in seed_assets())
        with session_scope() as session:
            for name, symbol, score in (
                ("Crypto rebounds", "BTC", 88.0),
                ("Stock dips", "AAPL", 61.0),
            ):
                mandate = Mandate(
                    name=name,
                    asset_class="crypto" if symbol == "BTC" else "stock",
                    universe_def={"type": "class"},
                    schedule="30 7 * * *",
                    score_weights={"rsi_extreme": 1.0},
                    notify="digest",
                )
                session.add(mandate)
                session.flush()
                session.add(
                    Candidate(
                        mandate_id=mandate.id,
                        asset_id=assets[symbol],
                        ts=datetime.now(UTC),
                        score=score,
                        signals=[{"key": "rsi_extreme", "label": "Oversold (RSI)",
                                  "triggered": True}],
                        context={},
                    )
                )

    def test_digest_groups_mandates_and_advances_window(self, email_env, outbox):
        _brief_off()  # the standalone digest is the LEGACY path (brief disabled)
        from app.discovery.notify import send_digest

        self.seed_digest_candidates()
        assert send_digest() == 2
        message = outbox.instances[0].sent[0]
        assert "2 new ideas" in message["Subject"]
        body = message.get_content()
        assert "Crypto rebounds" in body and "Stock dips" in body
        assert "BTC" in body and "AAPL" in body

        # the window advanced: nothing new -> no second email
        assert send_digest() == 0
        assert len(outbox.instances) == 1

    def test_failed_digest_does_not_advance_the_window(self, email_env, outbox, monkeypatch):
        _brief_off()
        from app.discovery import notify as notify_module
        from app.models import Notification

        self.seed_digest_candidates()

        def boom(subject, body):
            raise ConnectionError("smtp down")

        monkeypatch.setitem(notify_module.SENDERS, "email", boom)
        send_count = notify_module.send_digest()
        assert send_count == 2  # attempted
        with session_scope() as session:
            note = session.query(Notification).one()
            assert note.ok is False and "smtp down" in note.error

        # transport restored -> the same candidates are re-covered
        monkeypatch.setitem(notify_module.SENDERS, "email", lambda s, b: None)
        assert notify_module.send_digest() == 2

    def test_test_alert_endpoint(self, client, email_env, outbox):
        resp = client.post("/api/v1/mandates/test-alert", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["results"] == [{"channel": "email", "ok": True, "error": None}]
        assert "test alert" in outbox.instances[0].sent[0]["Subject"].lower()

    def test_test_alert_endpoint_400_when_unconfigured(self, client):
        resp = client.post("/api/v1/mandates/test-alert", headers=AUTH)
        assert resp.status_code == 400


class TestFundamentalsSignals:
    """Phase 5: a mandate can score candidates on financial health + quality."""

    def seed_stock(self, session, symbol: str, *, pe: float, roe: float,
                   healthy: bool) -> int:
        from datetime import date

        from app.models import Asset, Fundamentals

        asset = Asset(
            symbol=symbol, name=f"{symbol} Corp", asset_class="stock", currency="USD"
        )
        session.add(asset)
        session.flush()
        seed_bars(session, asset.id, np.linspace(90, 100, N_BARS))
        _upsert_metrics(
            session,
            asset.id,
            {"as_of": datetime.now(UTC).date(), "close": 100.0, "pe": pe, "roe": roe},
        )
        for year, factor in ((2024, 1.0), (2025, 1.2 if healthy else 0.8)):
            session.add(
                Fundamentals(
                    asset_id=asset.id,
                    period="annual",
                    report_date=date(year, 9, 30),
                    fiscal_year=year,
                    provider="fmp",
                    revenue=1000.0 * factor,
                    eps=5.0 * factor,
                    fcf=200.0 * factor,
                    gross_margin=0.40 * factor,
                    net_margin=0.20,
                    roe=roe * factor,
                    debt_to_equity=1.0 / factor,
                    pe=pe,
                    ps=4.0,
                    metrics={
                        "income": {
                            "netIncome": 250.0 * factor,
                            "weightedAverageShsOut": 1000.0 / factor,
                        },
                        "cashflow": {"operatingCashFlow": 300.0 * factor},
                        "balance": {"totalAssets": 2000.0},
                        "ratios": {"currentRatio": 1.2 * factor},
                        "key_metrics": {"returnOnInvestedCapital": roe},
                    },
                )
            )
        return asset.id

    @pytest.fixture
    def fundamentals_universe(self):
        seed_assets()
        with session_scope() as session:
            # one obviously healthy-and-cheap stock among 11 mediocre peers
            star = self.seed_stock(session, "STAR", pe=8.0, roe=0.35, healthy=True)
            for i in range(11):
                self.seed_stock(
                    session, f"BLND{i:02d}", pe=30.0 + i, roe=0.05, healthy=False
                )
        return star

    def test_scan_scores_fundamental_health(self, client, fundamentals_universe):
        response = client.post(
            "/api/v1/mandates",
            json={
                "name": "Sound businesses",
                "asset_class": "stock",
                "universe_def": {"type": "class"},
                "schedule": "30 7 * * 1-5",
                "score_weights": {"financial_health": 2.0, "quality_value": 1.0},
                "min_score": 60.0,
                "notify": "off",
            },
            headers=AUTH,
        )
        assert response.status_code == 201, response.text
        mandate_id = response.json()["id"]

        execute_scan(new_scan(mandate_id))

        listed = client.get(
            "/api/v1/candidates", params={"mandate_id": mandate_id}, headers=AUTH
        ).json()
        assert listed, "the healthy cheap stock should be nominated"
        top = listed[0]
        assert top["symbol"] == "STAR"
        by_key = {s["key"]: s for s in top["signals"]}
        health = by_key["financial_health"]
        assert health["score"] == 100.0
        assert health["triggered"] is True
        assert health["evidence"]["checks"]["profitable"] is True
        assert health["evidence"]["passed"] == health["evidence"]["evaluable"]
        quality = by_key["quality_value"]
        assert quality["triggered"] is True
        assert quality["evidence"]["peers"] == 12

    def test_fundamental_weights_rejected_for_crypto(self, client):
        response = client.post(
            "/api/v1/mandates",
            json={
                "name": "Crypto health?",
                "asset_class": "crypto",
                "universe_def": {"type": "class"},
                "schedule": "30 7 * * 1-5",
                "score_weights": {"financial_health": 1.0},
            },
            headers=AUTH,
        )
        assert response.status_code == 422
        [error] = response.json()["detail"]["errors"]
        assert error["path"] == "score_weights.financial_health"
        assert "does not apply to crypto" in error["error"]

    def test_signals_endpoint_lists_the_new_pack(self, client):
        listed = client.get("/api/v1/mandates/signals", headers=AUTH).json()
        by_key = {s["key"]: s for s in listed}
        assert by_key["financial_health"]["label"] == "Financially healthy"
        assert by_key["quality_value"]["label"] == "Quality at a fair price"
        assert by_key["financial_health"]["asset_classes"] == ["stock"]
        assert by_key["financial_health"]["supports_history_check"] is False
