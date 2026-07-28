import httpx
import pytest
import respx
import sqlalchemy as sa

from app.core.db import session_scope
from app.ingestion.eod import run_eod_all, run_eod_ingestion
from app.ingestion.seed import seed_assets
from app.models import Asset, IngestionRun
from tests.integration.conftest import mock_all_providers

pytestmark = pytest.mark.integration

# golden fixtures: tiingo 3 bars (AAPL/SPY/UUP/QQQ), binance 4 klines (BTC/ETH),
# twelvedata 3 bars (EURUSD/USDMXN)
EXPECTED_BARS = {
    "AAPL": 3,
    "BTC": 4,
    "ETH": 4,
    "EURUSD": 3,
    "USDMXN": 3,
    "SPY": 3,
    "UUP": 3,
    "QQQ": 3,
}


def bar_counts() -> dict[str, int]:
    with session_scope() as session:
        rows = session.execute(
            sa.text(
                "SELECT a.symbol, count(*) AS n FROM ohlcv o "
                "JOIN assets a ON a.id = o.asset_id GROUP BY a.symbol"
            )
        ).all()
        return {row.symbol: row.n for row in rows}


def get_runs(run_ids: list[int]) -> list[IngestionRun]:
    with session_scope() as session:
        runs = [session.get(IngestionRun, run_id) for run_id in run_ids]
        session.expunge_all()
        return runs


@respx.mock
def test_full_pipeline_and_idempotency(respx_mock):
    mock_all_providers(respx_mock)
    seed_assets()

    run_ids = run_eod_all()
    runs = get_runs(run_ids)
    assert [r.status for r in runs] == ["success", "success", "success"]
    assert all(r.rows_written > 0 for r in runs)
    assert all(r.finished_at is not None for r in runs)
    assert bar_counts() == EXPECTED_BARS

    # spec §4.3: ingestion is idempotent — rerun rewrites, row counts unchanged
    rerun_ids = run_eod_all()
    reruns = get_runs(rerun_ids)
    assert [r.status for r in reruns] == ["success", "success", "success"]
    assert bar_counts() == EXPECTED_BARS


@respx.mock
def test_partial_failure_records_errors(respx_mock):
    # register BEFORE the catch-all tiingo route: respx matches in insertion order.
    # 404 -> immediate ProviderError, no retries
    respx_mock.get(url__regex=r"https://api\.tiingo\.com/tiingo/daily/FAKE/prices.*").mock(
        return_value=httpx.Response(404)
    )
    mock_all_providers(respx_mock)
    seed_assets()
    with session_scope() as session:
        session.add(
            Asset(
                symbol="FAKE",
                name="Fake Corp",
                asset_class="stock",
                currency="USD",
                meta={"provider_symbols": {"tiingo": "FAKE"}},
            )
        )

    run_id = run_eod_ingestion("stock")
    run = get_runs([run_id])[0]
    assert run.status == "partial"
    assert run.symbols_ok == 4  # AAPL + benchmark/strip ETFs (SPY, UUP, QQQ)
    assert run.symbols_failed == 1
    assert "FAKE" in run.details["errors"]
    assert bar_counts()["AAPL"] == EXPECTED_BARS["AAPL"]  # the good symbol still landed


def test_provider_not_configured_fails_run(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("PROVIDER_STOCKS", "finnhub")  # accepted name, no adapter yet
    get_settings.cache_clear()
    from app.providers.registry import reset_registry

    reset_registry()
    seed_assets()

    run_id = run_eod_ingestion("stock")
    run = get_runs([run_id])[0]
    assert run.status == "failed"
    assert "_provider" in run.details["errors"]

    monkeypatch.setenv("PROVIDER_STOCKS", "tiingo")
    get_settings.cache_clear()
    reset_registry()


class TestAbandonedRuns:
    """A run row must never be left saying 'running'.

    Three eod_stock rows were stuck 'running' in the production DB — workers
    killed mid-run (the duplicate-delivery bug meant several were in flight at
    once) never reached _close_run, so the Settings page reported the job's last
    status as 'running' indefinitely.
    """

    def test_killed_run_is_closed_not_left_running(self, respx_mock):
        mock_all_providers(respx_mock)
        seed_assets()

        # a soft time limit / revoke lands as an exception inside the driver
        class SoftTimeLimit(BaseException):
            pass

        with session_scope() as session:
            before = session.query(IngestionRun).count()

        import app.ingestion.eod as eod_mod

        original = eod_mod.ingest_asset
        calls = {"n": 0}

        def kill_after_two(session, provider, asset, interval=None):
            calls["n"] += 1
            if calls["n"] > 2:
                raise SoftTimeLimit("time limit exceeded")
            return original(session, provider, asset)

        eod_mod.ingest_asset = kill_after_two
        try:
            with pytest.raises(SoftTimeLimit):
                run_eod_ingestion("stock")
        finally:
            eod_mod.ingest_asset = original

        with session_scope() as session:
            assert session.query(IngestionRun).count() == before + 1
            run = (
                session.query(IngestionRun)
                .order_by(IngestionRun.id.desc())
                .first()
            )
            assert run.status == "partial"  # two symbols did land
            assert run.finished_at is not None
            assert run.symbols_ok == 2
            assert "SoftTimeLimit" in run.details["errors"]["_run"]

    def test_stale_running_row_is_reaped_by_the_next_run(self, respx_mock):
        from datetime import UTC, datetime, timedelta

        from app.ingestion.eod import MAX_RUN_LIFETIME

        mock_all_providers(respx_mock)
        seed_assets()

        long_ago = datetime.now(UTC) - MAX_RUN_LIFETIME - timedelta(hours=1)
        with session_scope() as session:
            orphan = IngestionRun(
                job_name="eod_stock", status="running", started_at=long_ago
            )
            other_job = IngestionRun(
                job_name="news_pull", status="running", started_at=long_ago
            )
            session.add_all([orphan, other_job])
            session.flush()
            orphan_id, other_id = orphan.id, other_job.id

        run_eod_ingestion("stock")

        with session_scope() as session:
            reaped = session.get(IngestionRun, orphan_id)
            assert reaped.status == "failed"
            assert reaped.finished_at is not None
            assert "abandoned" in reaped.details["errors"]["_run"]
            # scoped to the job it opened: another job's row is left alone
            assert session.get(IngestionRun, other_id).status == "running"

    def test_live_run_is_not_reaped(self, respx_mock):
        from datetime import UTC, datetime, timedelta

        mock_all_providers(respx_mock)
        seed_assets()

        with session_scope() as session:
            live = IngestionRun(
                job_name="eod_stock",
                status="running",
                started_at=datetime.now(UTC) - timedelta(minutes=20),
            )
            session.add(live)
            session.flush()
            live_id = live.id

        run_eod_ingestion("stock")

        with session_scope() as session:
            assert session.get(IngestionRun, live_id).status == "running"
