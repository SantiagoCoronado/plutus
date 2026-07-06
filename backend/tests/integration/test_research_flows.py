"""Phase 2 integration: metrics refresh, resampling, fundamentals, news, watchlists, notes."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.core.db import session_scope
from app.ingestion.eod import upsert_candles
from app.ingestion.seed import seed_assets
from app.models import Asset, IngestionRun
from tests.integration.conftest import TEST_TOKEN, load_fixture

pytestmark = pytest.mark.integration

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}
N_BARS = 300


@pytest.fixture
def client():
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def seeded_with_bars():
    """Seed assets and write ~300 deterministic daily bars for each (no network)."""
    import numpy as np

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


def run_metrics():
    from app.analysis.metrics import run_metrics_refresh

    return run_metrics_refresh()


class TestMetricsRefresh:
    def test_refresh_populates_class_appropriate_metrics(self, seeded_with_bars):
        run_id = run_metrics()
        with session_scope() as session:
            run = session.get(IngestionRun, run_id)
            assert run.status == "success"
            rows = session.execute(
                sa.text(
                    "SELECT a.symbol, a.asset_class, m.rsi_14, m.obv, m.vwap_20, m.rs_3m, "
                    "m.benchmark_symbol FROM asset_metrics m JOIN assets a ON a.id = m.asset_id"
                )
            ).all()
        by_symbol = {r.symbol: r for r in rows}
        assert len(by_symbol) == 8  # AAPL, BTC, ETH, EURUSD, USDMXN, SPY, UUP, QQQ
        # forex: volume metrics NULL, price metrics present
        eurusd = by_symbol["EURUSD"]
        assert eurusd.rsi_14 is not None
        assert eurusd.obv is None and eurusd.vwap_20 is None
        # benchmark wiring: AAPL vs SPY; SPY (own benchmark) has NULL rs
        assert by_symbol["AAPL"].benchmark_symbol == "SPY"
        assert by_symbol["AAPL"].rs_3m is not None
        assert by_symbol["SPY"].benchmark_symbol is None
        assert by_symbol["SPY"].rs_3m is None

    def test_refresh_is_idempotent(self, seeded_with_bars):
        run_metrics()
        with session_scope() as session:
            first = session.execute(sa.text("SELECT count(*) FROM asset_metrics")).scalar()
        run_metrics()
        with session_scope() as session:
            second = session.execute(sa.text("SELECT count(*) FROM asset_metrics")).scalar()
        assert first == second == 8


class TestResampling:
    def test_weekly_and_monthly_buckets(self, client, seeded_with_bars):
        asset_id = seeded_with_bars["AAPL"]
        daily = client.get(f"/api/v1/assets/{asset_id}/ohlcv?interval=1d", headers=AUTH).json()
        weekly = client.get(f"/api/v1/assets/{asset_id}/ohlcv?interval=1w", headers=AUTH).json()
        monthly = client.get(f"/api/v1/assets/{asset_id}/ohlcv?interval=1M", headers=AUTH).json()

        n_daily = len(daily["candles"])
        n_weekly = len(weekly["candles"])
        n_monthly = len(monthly["candles"])
        assert n_daily == N_BARS
        assert n_daily / 7 - 1 <= n_weekly <= n_daily / 7 + 2
        assert 9 <= n_monthly <= 12  # ~300 days

        # bucket OHLC semantics: high is the max of the bucket, volume the sum
        first_week = weekly["candles"][1]  # a full interior bucket
        assert first_week["high"] >= first_week["open"]
        assert first_week["high"] >= first_week["close"]
        assert first_week["volume"] > 1e6  # summed daily volumes

    def test_indicator_series_lwc_shape(self, client, seeded_with_bars):
        asset_id = seeded_with_bars["AAPL"]
        resp = client.get(
            f"/api/v1/assets/{asset_id}/indicators?keys=sma_20,macd,rsi_14", headers=AUTH
        )
        assert resp.status_code == 200
        series = resp.json()["series"]
        assert {"sma_20", "macd", "rsi_14"} <= set(series)
        point = series["sma_20"][0]
        assert set(point) == {"time", "value"}
        assert isinstance(point["time"], int)
        assert set(series["macd"]) == {"macd", "macd_signal", "macd_hist"}

    def test_indicator_unknown_key_422(self, client, seeded_with_bars):
        asset_id = seeded_with_bars["AAPL"]
        resp = client.get(f"/api/v1/assets/{asset_id}/indicators?keys=bogus", headers=AUTH)
        assert resp.status_code == 422
        assert "valid_keys" in resp.json()["detail"]


class TestFundamentalsFlow:
    @respx.mock(base_url="https://financialmodelingprep.com/stable")
    def test_refresh_and_read(self, respx_mock, client, seeded_with_bars):
        respx_mock.get("/income-statement").mock(
            return_value=httpx.Response(200, json=load_fixture("fmp_income.json"))
        )
        respx_mock.get("/balance-sheet-statement").mock(
            return_value=httpx.Response(200, json=load_fixture("fmp_balance.json"))
        )
        respx_mock.get("/cash-flow-statement").mock(
            return_value=httpx.Response(200, json=load_fixture("fmp_cashflow.json"))
        )
        respx_mock.get("/ratios").mock(
            return_value=httpx.Response(200, json=load_fixture("fmp_ratios.json"))
        )
        respx_mock.get("/key-metrics").mock(
            return_value=httpx.Response(200, json=load_fixture("fmp_key_metrics.json"))
        )
        respx_mock.get("/profile").mock(
            return_value=httpx.Response(200, json=load_fixture("fmp_profile.json"))
        )
        import os

        os.environ["FMP_API_KEY"] = "test-key"
        from app.core.config import get_settings

        get_settings.cache_clear()
        from app.ingestion.fundamentals import run_fundamentals_refresh

        run_id = run_fundamentals_refresh()
        with session_scope() as session:
            run = session.get(IngestionRun, run_id)
            assert run.status == "success", run.details
            assert run.rows_written == 2  # AAPL's 2 annual periods; ETF benchmarks are profile-only
        asset_id = seeded_with_bars["AAPL"]
        rows = client.get(f"/api/v1/assets/{asset_id}/fundamentals", headers=AUTH).json()
        assert len(rows) == 2
        assert rows[0]["revenue"] == 416161000000
        assert rows[0]["metrics"]["income"]["netIncome"] == 112010000000

        # profile landed in asset.meta for the metrics job
        with session_scope() as session:
            asset = session.get(Asset, asset_id)
            assert asset.meta["profile"]["market_cap"] == 4561230000000

    def test_refresh_endpoint_rejects_crypto(self, client, seeded_with_bars):
        resp = client.post(
            f"/api/v1/assets/{seeded_with_bars['BTC']}/fundamentals/refresh", headers=AUTH
        )
        assert resp.status_code == 422


class TestNewsFlow:
    @respx.mock(base_url="https://finnhub.io/api/v1")
    def test_pull_dedup_and_read(self, respx_mock, client, seeded_with_bars):
        respx_mock.get("/company-news").mock(
            return_value=httpx.Response(200, json=load_fixture("finnhub_company_news.json"))
        )
        import os

        os.environ["FINNHUB_API_KEY"] = "test-key"
        from app.core.config import get_settings

        get_settings.cache_clear()
        from app.ingestion.news import run_news_pull
        from app.providers.registry import reset_registry

        reset_registry()

        run_news_pull()
        with session_scope() as session:
            first_count = session.execute(sa.text("SELECT count(*) FROM news_items")).scalar()
            # same URLs seen from every stock/etf symbol -> ticker merge, not duplicates
            tickers = session.execute(
                sa.text("SELECT tickers FROM news_items ORDER BY ts DESC LIMIT 1")
            ).scalar()
        assert first_count == 2  # 2 valid fixture URLs
        assert set(tickers) == {"AAPL", "SPY", "UUP", "QQQ"}

        run_news_pull()  # cache TTL may serve, but dedup must hold regardless
        with session_scope() as session:
            second_count = session.execute(sa.text("SELECT count(*) FROM news_items")).scalar()
        assert second_count == first_count

        rows = client.get(
            f"/api/v1/assets/{seeded_with_bars['AAPL']}/news?days=90", headers=AUTH
        ).json()
        assert len(rows) == 2

        # crypto asset: no news, clean empty response
        btc = client.get(
            f"/api/v1/assets/{seeded_with_bars['BTC']}/news?days=90", headers=AUTH
        ).json()
        assert btc == []


class TestWatchlistsAndNotes:
    def test_watchlist_crud_and_items(self, client, seeded_with_bars):
        run_metrics()  # so items carry close/return_1d
        lists = client.get("/api/v1/watchlists", headers=AUTH).json()
        default = next(w for w in lists if w["name"] == "Default")

        asset_id = seeded_with_bars["AAPL"]
        resp = client.post(
            f"/api/v1/watchlists/{default['id']}/items",
            json={"asset_id": asset_id},
            headers=AUTH,
        )
        assert resp.status_code == 201
        # idempotent add
        client.post(
            f"/api/v1/watchlists/{default['id']}/items",
            json={"asset_id": asset_id},
            headers=AUTH,
        )

        lists = client.get("/api/v1/watchlists", headers=AUTH).json()
        default = next(w for w in lists if w["name"] == "Default")
        assert len(default["items"]) == 1
        item = default["items"][0]
        assert item["symbol"] == "AAPL"
        assert item["close"] is not None and item["return_1d"] is not None

        assert (
            client.delete(
                f"/api/v1/watchlists/{default['id']}/items/{asset_id}", headers=AUTH
            ).status_code
            == 204
        )

        # named list lifecycle + duplicate-name conflict
        created = client.post("/api/v1/watchlists", json={"name": "Ideas"}, headers=AUTH)
        assert created.status_code == 201
        dup = client.post("/api/v1/watchlists", json={"name": "Ideas"}, headers=AUTH)
        assert dup.status_code == 409
        assert (
            client.delete(f"/api/v1/watchlists/{created.json()['id']}", headers=AUTH).status_code
            == 204
        )

    def test_notes_crud(self, client, seeded_with_bars):
        asset_id = seeded_with_bars["AAPL"]
        created = client.post(
            f"/api/v1/assets/{asset_id}/notes",
            json={"title": "Thesis", "body_md": "# AAPL\nservices growth"},
            headers=AUTH,
        )
        assert created.status_code == 201
        note = created.json()
        assert note["source"] == "user"

        updated = client.patch(
            f"/api/v1/assets/{asset_id}/notes/{note['id']}",
            json={"body_md": "# AAPL\nrevised"},
            headers=AUTH,
        )
        assert updated.status_code == 200
        assert updated.json()["body_md"].endswith("revised")

        notes = client.get(f"/api/v1/assets/{asset_id}/notes", headers=AUTH).json()
        assert len(notes) == 1

        assert (
            client.delete(
                f"/api/v1/assets/{asset_id}/notes/{note['id']}", headers=AUTH
            ).status_code
            == 204
        )
        assert client.get(f"/api/v1/assets/{asset_id}/notes", headers=AUTH).json() == []
