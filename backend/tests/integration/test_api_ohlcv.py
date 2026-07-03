import pytest
import respx
from fastapi.testclient import TestClient

from app.ingestion.eod import run_eod_all
from app.ingestion.seed import seed_assets
from tests.integration.conftest import TEST_TOKEN, mock_all_providers

pytestmark = pytest.mark.integration

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture
def client():
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def ingested():
    with respx.mock as respx_mock:
        mock_all_providers(respx_mock)
        assets = dict((symbol, asset_id) for asset_id, symbol in seed_assets())
        run_eod_all()
    return assets


def test_health_reports_ok(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["db"] == "ok" and body["redis"] == "ok"


def test_ohlcv_returns_ordered_candles(client, ingested):
    asset_id = ingested["EURUSD"]
    resp = client.get(f"/api/v1/assets/{asset_id}/ohlcv?interval=1d", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["asset_id"] == asset_id
    candles = body["candles"]
    assert len(candles) == 3
    assert [c["ts"] for c in candles] == sorted(c["ts"] for c in candles)
    assert candles[0]["volume"] is None  # forex

    # date-range filter
    resp = client.get(
        f"/api/v1/assets/{asset_id}/ohlcv?interval=1d&start=2026-06-30", headers=AUTH
    )
    assert len(resp.json()["candles"]) == 2


def test_ohlcv_404_for_unknown_asset(client):
    assert client.get("/api/v1/assets/424242/ohlcv", headers=AUTH).status_code == 404


def test_search_returns_tracked_flag(client, ingested):
    # provider search endpoints are not mocked here: the route must degrade to local-only
    resp = client.get("/api/v1/assets/search?q=appl", headers=AUTH)
    assert resp.status_code == 200
    results = resp.json()["results"]
    aapl = next(r for r in results if r["symbol"] == "AAPL")
    assert aapl["tracked"] is True
    assert aapl["asset_id"] == ingested["AAPL"]


def test_track_asset_upserts(client):
    body = {
        "symbol": "msft",
        "name": "Microsoft Corp.",
        "asset_class": "stock",
        "exchange": "NASDAQ",
        "currency": "USD",
        "meta": {"provider_symbols": {"tiingo": "MSFT"}},
    }
    resp = client.post("/api/v1/assets", json=body, headers=AUTH)
    assert resp.status_code == 201
    created = resp.json()
    assert created["symbol"] == "MSFT"  # normalized upper
    assert created["is_active"] is True

    # idempotent re-track
    resp2 = client.post("/api/v1/assets", json=body, headers=AUTH)
    assert resp2.status_code == 201
    assert resp2.json()["id"] == created["id"]


def test_ingestion_runs_listed(client, ingested):
    resp = client.get("/api/v1/ingestion/runs", headers=AUTH)
    assert resp.status_code == 200
    runs = resp.json()
    assert len(runs) == 3
    assert {r["status"] for r in runs} == {"success"}
    assert all(r["rows_written"] > 0 for r in runs)
