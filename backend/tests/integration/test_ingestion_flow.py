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

# golden fixtures: tiingo 3 bars (AAPL/SPY/UUP), binance 4 klines, twelvedata 3 bars
EXPECTED_BARS = {"AAPL": 3, "BTC": 4, "EURUSD": 3, "USDMXN": 3, "SPY": 3, "UUP": 3}


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
    assert run.symbols_ok == 3  # AAPL + benchmark ETFs (SPY, UUP)
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
