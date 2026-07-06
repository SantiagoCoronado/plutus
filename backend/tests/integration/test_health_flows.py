"""Phase 7 M5 integration: GET /health/ingestion over seeded ingestion_runs
(fresh green / stale amber+red / failed news) and real Redis budget counters."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.db import session_scope
from app.health.aggregate import EXPECTED_CADENCE
from app.models import IngestionRun
from tests.integration.conftest import TEST_TOKEN

pytestmark = pytest.mark.integration

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}

JOB_KEYS = {
    "job_name",
    "provider",
    "asset_class",
    "last_status",
    "last_run_at",
    "last_success_at",
    "staleness",
    "rows_written",
    "symbols_ok",
    "symbols_failed",
    "note",
}


@pytest.fixture
def client():
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def seed_run(session, job_name, *, status="success", age=timedelta(hours=1), provider=None):
    now = datetime.now(UTC)
    session.add(
        IngestionRun(
            job_name=job_name,
            provider=provider,
            asset_class=None,
            started_at=now - age - timedelta(minutes=5),
            finished_at=now - age,
            status=status,
            rows_written=10,
            symbols_ok=3,
            symbols_failed=0 if status == "success" else 1,
        )
    )


def seed_all_fresh(session):
    for job_name in EXPECTED_CADENCE:
        seed_run(session, job_name, age=timedelta(minutes=30))


def get_health(client):
    resp = client.get("/api/v1/health/ingestion", headers=AUTH)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_requires_auth(client):
    assert client.get("/api/v1/health/ingestion").status_code == 401


def test_all_fresh_is_green_with_full_shape(client):
    with session_scope() as session:
        seed_all_fresh(session)

    body = get_health(client)
    assert set(body) == {"status", "jobs", "budgets"}
    assert body["status"] == "green"

    jobs = {job["job_name"]: job for job in body["jobs"]}
    assert set(jobs) == set(EXPECTED_CADENCE)
    for job in jobs.values():
        assert set(job) == JOB_KEYS
        assert job["staleness"] == "green"
        assert job["last_status"] == "success"
        assert job["last_run_at"] is not None
        assert job["last_success_at"] is not None

    # every budget row is well-formed even with untouched counters
    assert body["budgets"], "PROVIDER_LIMITS defines day/month budgets"
    for budget in body["budgets"]:
        assert set(budget) == {"provider", "window", "used", "budget", "pct"}
        assert budget["window"] in ("day", "month")
        assert budget["used"] == 0
        assert budget["pct"] == 0.0


def test_never_ran_is_amber(client):
    body = get_health(client)  # empty ingestion_runs
    assert body["status"] == "amber"
    for job in body["jobs"]:
        assert job["staleness"] == "amber"
        assert job["note"] == "never ran"
        assert job["last_run_at"] is None


def test_stale_and_failed_runs_roll_up_to_red(client):
    with session_scope() as session:
        for job_name in ("eod_forex", "metrics_refresh", "fundamentals_refresh"):
            seed_run(session, job_name, age=timedelta(minutes=30))
        # stale eod jobs: 40h -> amber, 60h -> red
        seed_run(session, "eod_crypto", age=timedelta(hours=40), provider="binance")
        seed_run(session, "eod_stock", age=timedelta(hours=60), provider="tiingo")
        # news: failed just now, last success 3h ago -> amber, failure visible
        seed_run(session, "news_pull", age=timedelta(hours=3))
        seed_run(session, "news_pull", status="failed", age=timedelta(minutes=5))

    body = get_health(client)
    jobs = {job["job_name"]: job for job in body["jobs"]}
    assert jobs["eod_crypto"]["staleness"] == "amber"
    assert jobs["eod_stock"]["staleness"] == "red"
    assert jobs["news_pull"]["staleness"] == "amber"
    assert jobs["news_pull"]["last_status"] == "failed"
    assert jobs["eod_forex"]["staleness"] == "green"
    assert body["status"] == "red"


def test_budget_counters_appear_in_response(client):
    from app.providers.registry import _shared_redis

    redis_client = _shared_redis()
    now = datetime.now(UTC)
    day_key = f"budget:tiingo:day:{now:%Y%m%d}"
    month_key = f"budget:coingecko:month:{now:%Y%m}"
    redis_client.set(day_key, 450)
    redis_client.set(month_key, 900)
    try:
        with session_scope() as session:
            seed_all_fresh(session)
        body = get_health(client)
        budgets = {(b["provider"], b["window"]): b for b in body["budgets"]}
        assert budgets[("tiingo", "day")] == {
            "provider": "tiingo",
            "window": "day",
            "used": 450,
            "budget": 900,
            "pct": 50.0,
        }
        assert budgets[("coingecko", "month")]["used"] == 900
        assert budgets[("coingecko", "month")]["pct"] == 10.0
    finally:
        redis_client.delete(day_key, month_key)
