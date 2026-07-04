"""Phase 3 integration: backtest rows through the API + runner, artifact serving."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.backtest.runner import execute_backtest
from app.core.config import get_settings
from app.core.db import session_scope
from app.ingestion.eod import upsert_candles
from app.ingestion.seed import seed_assets
from tests.integration.conftest import TEST_TOKEN

pytestmark = pytest.mark.integration

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}
N_BARS = 400  # > WARMUP_BARS(300) + a trading window

MATCH_ALL_STOCKS = {"field": "rsi_14", "op": "between", "value": [0, 100]}


@pytest.fixture
def client():
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def artifacts_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture
def seeded_with_bars():
    assets = dict((symbol, asset_id) for asset_id, symbol in seed_assets())
    end = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    with session_scope() as session:
        for offset, (symbol, asset_id) in enumerate(sorted(assets.items())):
            t = np.arange(N_BARS, dtype=float)
            close = 100 + offset * 10 + 0.05 * t + 10 * np.sin(t / 12 + offset)
            rows = []
            for i in range(N_BARS):
                ts = end - timedelta(days=N_BARS - i)
                is_forex = symbol == "EURUSD"
                rows.append(
                    {
                        "asset_id": asset_id,
                        "interval": "1d",
                        "ts": ts,
                        "open": close[i] - 0.5,
                        "high": close[i] + 2.0,
                        "low": close[i] - 2.5,
                        "close": close[i],
                        "volume": None if is_forex else 1e6 + i,
                    }
                )
            upsert_candles(session, rows)
    return assets


class TestScreenBacktestFlow:
    def test_end_to_end(self, client, seeded_with_bars, artifacts_dir):
        created = client.post(
            "/api/v1/backtests/screen",
            json={"ast": MATCH_ALL_STOCKS, "asset_class": "stock", "holding_days": 20},
            headers=AUTH,
        )
        assert created.status_code == 201
        backtest_id = created.json()["id"]
        assert created.json()["status"] == "queued"

        execute_backtest(backtest_id)

        resp = client.get(f"/api/v1/backtests/{backtest_id}", headers=AUTH)
        body = resp.json()
        assert body["status"] == "done", body["error"]
        stats = body["stats"]
        assert stats["universe_size"] == 1  # AAPL is the only pure stock
        assert stats["rebalances"] >= 3
        assert stats["cagr"] is not None and stats["max_drawdown"] <= 0
        assert stats["benchmark_symbol"] == "SPY" and stats["benchmark"] is not None
        assert len(body["equity_curve"]["portfolio"]) > 10
        assert len(body["equity_curve"]["benchmark"]) > 10
        assert body["trade_list"][0]["symbols"] == ["AAPL"]

    def test_rejects_non_pit_fields(self, client, seeded_with_bars):
        resp = client.post(
            "/api/v1/backtests/screen",
            json={"ast": {"field": "pe", "op": "<", "value": 20}, "asset_class": "stock"},
            headers=AUTH,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "pe" not in detail["backtestable_fields"]
        assert "rsi_14" in detail["backtestable_fields"]

    def test_requires_asset_class(self, client, seeded_with_bars):
        resp = client.post(
            "/api/v1/backtests/screen", json={"ast": MATCH_ALL_STOCKS}, headers=AUTH
        )
        assert resp.status_code == 422

    def test_saved_screen_source(self, client, seeded_with_bars, artifacts_dir):
        screen = client.post(
            "/api/v1/screens",
            json={"name": "all stocks", "ast": MATCH_ALL_STOCKS, "asset_class": "stock"},
            headers=AUTH,
        ).json()
        created = client.post(
            "/api/v1/backtests/screen", json={"screen_id": screen["id"]}, headers=AUTH
        )
        assert created.status_code == 201
        assert created.json()["screen_id"] == screen["id"]

    def test_empty_universe_fails_gracefully(self, client, artifacts_dir):
        created = client.post(
            "/api/v1/backtests/screen",
            json={"ast": MATCH_ALL_STOCKS, "asset_class": "crypto"},
            headers=AUTH,
        )
        backtest_id = created.json()["id"]
        execute_backtest(backtest_id)
        body = client.get(f"/api/v1/backtests/{backtest_id}", headers=AUTH).json()
        assert body["status"] == "failed"
        assert "no active crypto assets" in body["error"]


class TestStrategyBacktestFlow:
    def test_end_to_end_with_artifact(self, client, seeded_with_bars, artifacts_dir):
        created = client.post(
            "/api/v1/backtests/strategy",
            json={
                "asset_id": seeded_with_bars["AAPL"],
                "entry": {"field": "close", "op": "crosses_above", "value": {"field": "sma_20"}},
                "exit": {"field": "close", "op": "crosses_below", "value": {"field": "sma_20"}},
            },
            headers=AUTH,
        )
        assert created.status_code == 201
        backtest_id = created.json()["id"]

        execute_backtest(backtest_id)

        body = client.get(f"/api/v1/backtests/{backtest_id}", headers=AUTH).json()
        assert body["status"] == "done", body["error"]
        assert body["stats"]["n_trades"] >= 2  # sine wave crosses its sma repeatedly
        assert body["trade_list"][0]["entry_price"] is not None
        assert body["stats"]["symbol"] == "AAPL"

        # artifact written under the overridden dir and served with auth
        assert body["artifact_path"] and Path(body["artifact_path"]).is_file()
        report = client.get(f"/api/v1/backtests/{backtest_id}/report", headers=AUTH)
        assert report.status_code == 200
        assert "text/html" in report.headers["content-type"]

    def test_rejects_bad_condition(self, client, seeded_with_bars):
        resp = client.post(
            "/api/v1/backtests/strategy",
            json={
                "asset_id": seeded_with_bars["AAPL"],
                "entry": {"field": "pe", "op": "<", "value": 10},
                "exit": {"field": "close", "op": "<", "value": 1},
            },
            headers=AUTH,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["context"] == "entry"


class TestBacktestHousekeeping:
    def test_list_and_delete(self, client, seeded_with_bars, artifacts_dir):
        created = client.post(
            "/api/v1/backtests/screen",
            json={"ast": MATCH_ALL_STOCKS, "asset_class": "stock"},
            headers=AUTH,
        )
        backtest_id = created.json()["id"]

        listed = client.get("/api/v1/backtests?kind=screen", headers=AUTH).json()
        assert [b["id"] for b in listed] == [backtest_id]

        assert client.delete(f"/api/v1/backtests/{backtest_id}", headers=AUTH).status_code == 204
        assert client.get(f"/api/v1/backtests/{backtest_id}", headers=AUTH).status_code == 404
