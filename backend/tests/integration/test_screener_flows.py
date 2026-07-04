"""Phase 3 integration: screen CRUD, ad-hoc + saved runs, NULL exclusion, class scoping."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.analysis.metrics import _upsert_metrics
from app.core.db import session_scope
from app.ingestion.seed import seed_assets
from tests.integration.conftest import TEST_TOKEN

pytestmark = pytest.mark.integration

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}

OVERSOLD_UPTREND = {
    "all": [
        {"field": "rsi_14", "op": "<", "value": 40},
        {"field": "close", "op": ">", "value": {"field": "sma_200"}},
    ]
}


@pytest.fixture
def client():
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def seeded_with_metrics():
    """Seed assets and write hand-picked asset_metrics rows (deterministic screening)."""
    assets = dict((symbol, asset_id) for asset_id, symbol in seed_assets())
    as_of = datetime.now(UTC).date()
    # AAPL: oversold + above sma_200 -> matches; SPY: overbought; UUP: oversold but below
    # sma_200; BTC: crypto oversold match; EURUSD: rsi NULL (must never match, even negated)
    rows = {
        "AAPL": {"rsi_14": 35.0, "close": 200.0, "sma_200": 180.0, "pe": 30.0},
        "SPY": {"rsi_14": 65.0, "close": 500.0, "sma_200": 480.0, "pe": None},
        "UUP": {"rsi_14": 30.0, "close": 27.0, "sma_200": 29.0, "pe": None},
        "BTC": {"rsi_14": 38.0, "close": 60000.0, "sma_200": 50000.0, "pe": None},
        "EURUSD": {"rsi_14": None, "close": 1.1, "sma_200": 1.0, "pe": None},
    }
    with session_scope() as session:
        for symbol, metrics in rows.items():
            _upsert_metrics(session, assets[symbol], {"as_of": as_of, **metrics})
    return assets


class TestScreenCrud:
    def test_lifecycle(self, client):
        body = {"name": "Oversold uptrend", "ast": OVERSOLD_UPTREND, "asset_class": "stock"}
        created = client.post("/api/v1/screens", json=body, headers=AUTH)
        assert created.status_code == 201
        screen_id = created.json()["id"]

        assert client.post("/api/v1/screens", json=body, headers=AUTH).status_code == 409

        listed = client.get("/api/v1/screens", headers=AUTH).json()
        assert [s["name"] for s in listed] == ["Oversold uptrend"]

        updated = client.put(
            f"/api/v1/screens/{screen_id}",
            json={**body, "name": "Oversold uptrend v2", "asset_class": None},
            headers=AUTH,
        )
        assert updated.status_code == 200
        assert updated.json()["asset_class"] is None

        assert client.delete(f"/api/v1/screens/{screen_id}", headers=AUTH).status_code == 204
        assert client.get(f"/api/v1/screens/{screen_id}", headers=AUTH).status_code == 404

    def test_create_rejects_bad_ast(self, client):
        resp = client.post(
            "/api/v1/screens",
            json={"name": "bad", "ast": {"field": "bogus", "op": ">", "value": 1}},
            headers=AUTH,
        )
        assert resp.status_code == 422
        errors = resp.json()["detail"]["errors"]
        assert errors[0]["path"] == "$" and "bogus" in errors[0]["error"]

    def test_fields_endpoint(self, client):
        fields = {f["name"]: f for f in client.get("/api/v1/screens/fields", headers=AUTH).json()}
        assert fields["rsi_14"]["backtestable"] and not fields["rsi_14"]["fundamental"]
        assert fields["pe"]["fundamental"] and not fields["pe"]["backtestable"]


class TestScreenRuns:
    def test_adhoc_run_scoped_to_stocks(self, client, seeded_with_metrics):
        resp = client.post(
            "/api/v1/screens/run",
            json={"ast": OVERSOLD_UPTREND, "asset_class": "stock"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["results"][0]["symbol"] == "AAPL"
        assert set(body["columns"]) == {"close", "rsi_14", "sma_200"}
        assert body["results"][0]["values"]["rsi_14"] == 35.0

    def test_adhoc_run_all_classes(self, client, seeded_with_metrics):
        resp = client.post("/api/v1/screens/run", json={"ast": OVERSOLD_UPTREND}, headers=AUTH)
        assert [hit["symbol"] for hit in resp.json()["results"]] == ["AAPL", "BTC"]

    def test_null_metric_never_matches_even_negated(self, client, seeded_with_metrics):
        for ast in (
            {"field": "rsi_14", "op": "<", "value": 40},
            {"not": {"field": "rsi_14", "op": "<", "value": 40}},
        ):
            resp = client.post(
                "/api/v1/screens/run", json={"ast": ast, "asset_class": "forex"}, headers=AUTH
            )
            assert resp.json()["count"] == 0  # EURUSD rsi_14 is NULL

    def test_saved_run_uses_screen_scope(self, client, seeded_with_metrics):
        created = client.post(
            "/api/v1/screens",
            json={"name": "oversold", "ast": OVERSOLD_UPTREND, "asset_class": "crypto"},
            headers=AUTH,
        )
        screen_id = created.json()["id"]
        resp = client.post(f"/api/v1/screens/{screen_id}/run", headers=AUTH)
        assert [hit["symbol"] for hit in resp.json()["results"]] == ["BTC"]

    def test_run_rejects_bad_ast_with_422(self, client, seeded_with_metrics):
        resp = client.post(
            "/api/v1/screens/run",
            json={"ast": {"field": "rsi_14", "op": "between", "value": [70, 30]}},
            headers=AUTH,
        )
        assert resp.status_code == 422
        assert "low <= high" in resp.json()["detail"]["errors"][0]["error"]
