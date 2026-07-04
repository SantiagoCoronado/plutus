"""Phase 4 integration: mandate scans end-to-end, dedup, and the beat dispatcher."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.analysis.metrics import _upsert_metrics
from app.core.db import session_scope
from app.discovery.runner import dispatch_due_mandates, execute_scan
from app.ingestion.eod import upsert_candles
from app.ingestion.seed import seed_assets
from app.models import Candidate, Mandate, Scan

pytestmark = pytest.mark.integration

N_BARS = 400


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
        just_ran = datetime.now(UTC) - timedelta(seconds=30)
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
