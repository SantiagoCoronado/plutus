"""Phase 7 M6 integration: the §9.1 dashboard aggregate + heatmap tiles.

Seeds a small but complete world (bars + asset_metrics + a position + a candidate
+ a memo + an armed alert + a watchlist) and asserts the composed shape. Also
proves an empty database returns 200 rather than 500.
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.analysis.metrics import _upsert_metrics
from app.core.db import session_scope
from app.ingestion.seed import seed_assets
from app.models import (
    AlertRule,
    Candidate,
    Mandate,
    Notification,
    Scan,
    Watchlist,
    WatchlistItem,
)
from tests.integration.conftest import TEST_TOKEN
from tests.integration.test_portfolio_flows import (
    iso_days_ago,
    make_account,
    post_txn,
    seed_flat_bars,
)

pytestmark = pytest.mark.integration

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}

# return columns are fractions; the endpoint returns change_pct as a percent (x100)
METRICS = {
    "AAPL": {"close": 240.0, "return_1d": 0.012, "return_1w": 0.03, "return_1m": -0.05,
             "return_ytd": 0.20, "market_cap": 3.0e12},
    "SPY": {"close": 550.0, "return_1d": 0.004, "return_1w": 0.01, "return_1m": 0.02,
            "return_ytd": 0.10, "market_cap": 5.0e11},
}  # fmt: skip


@pytest.fixture
def client():
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def asset_ids():
    return {symbol: asset_id for asset_id, symbol in seed_assets()}


@pytest.fixture
def dashboard_seed(client, asset_ids):
    """A funded position, scored candidate, memo, armed alert, and a 2-item watchlist."""
    seed_flat_bars(asset_ids)  # AAPL 200->240, SPY 500->550, USDMXN pinned at 20
    as_of = datetime.now(UTC).date()
    now = datetime.now(UTC)

    bitso = make_account(client, name="Bitso", type="exchange")
    post_txn(client, account_id=bitso["id"], type="deposit", quantity=5000, ts=iso_days_ago(60))
    post_txn(client, account_id=bitso["id"], type="buy", asset_id=asset_ids["AAPL"],
             quantity=10, price=190, ts=iso_days_ago(50))

    with session_scope() as session:
        for symbol, metrics in METRICS.items():
            _upsert_metrics(session, asset_ids[symbol], {"as_of": as_of, **metrics})

        mandate = Mandate(
            name="Oversold stocks",
            asset_class="stock",
            universe_def={"type": "class"},
            schedule="30 7 * * 1-5",
            score_weights={"rsi_extreme": 1.0},
        )
        session.add(mandate)
        session.flush()
        scan = Scan(mandate_id=mandate.id, status="done", started_at=now, finished_at=now)
        session.add(scan)
        session.flush()
        session.add(
            Candidate(
                mandate_id=mandate.id,
                scan_id=scan.id,
                asset_id=asset_ids["AAPL"],
                ts=now,
                score=87.0,
                signals=[
                    {"key": "rsi_extreme", "label": "RSI extreme", "score": 90,
                     "weight": 1.0, "triggered": True},
                    {"key": "breakout", "label": "Breakout", "score": 20,
                     "weight": 1.0, "triggered": False},
                ],
                status="new",
                context={},
            )
        )
        session.add(
            Notification(
                channel="email",
                kind="memo",
                subject="Overnight scan brief",
                body="**AAPL** flagged by Oversold stocks.",
                ok=True,
            )
        )
        session.add(
            AlertRule(
                asset_id=asset_ids["AAPL"], condition="above", threshold=300, status="armed"
            )
        )
        # a disabled rule must NOT count toward armed_alerts
        session.add(
            AlertRule(
                asset_id=asset_ids["SPY"], condition="below", threshold=100, status="disabled"
            )
        )

        default_id = session.scalar(select(Watchlist.id).where(Watchlist.name == "Default"))
        session.add(WatchlistItem(watchlist_id=default_id, asset_id=asset_ids["AAPL"]))
        session.add(WatchlistItem(watchlist_id=default_id, asset_id=asset_ids["BTC"]))

    return asset_ids


class TestDashboardAggregate:
    def test_shape_and_values(self, client, dashboard_seed):
        data = client.get("/api/v1/dashboard", params={"currency": "USD"}, headers=AUTH).json()

        # market strip: straight from MARKET_STRIP, 7 entries (VIX dropped)
        strip = data["market_strip"]
        assert len(strip) == 7
        assert [e["symbol"] for e in strip] == [
            "SPY", "QQQ", "BTC", "ETH", "EURUSD", "USDMXN", "UUP",
        ]
        assert all({"label", "symbol", "asset_class"} <= e.keys() for e in strip)

        # portfolio: a real AAPL position drives a positive value
        assert data["portfolio"]["currency"] == "USD"
        assert data["portfolio"]["value"] > 0
        assert len(data["portfolio"]["series_30d"]) > 0

        # ytd: SPY rose over the seeded window
        assert data["ytd"]["benchmark_symbol"] == "SPY"
        assert data["ytd"]["benchmark_return_pct"] is not None
        assert data["ytd"]["benchmark_return_pct"] > 0

        # candidates: top capped at 5, our one candidate present
        assert data["candidates"]["new_count"] >= 1
        assert len(data["candidates"]["top"]) <= 5
        top = data["candidates"]["top"][0]
        assert top["symbol"] == "AAPL"
        assert top["score"] == pytest.approx(87.0)
        assert top["signals_summary"] == ["RSI extreme"]  # only the triggered one

        # armed alerts: exactly one (the disabled rule is excluded)
        assert data["armed_alerts"] == 1

        # agent brief: the memo notification, reused not regenerated
        assert data["agent_brief"]["subject"] == "Overnight scan brief"

        assert data["last_scan_at"] is not None
        assert data["ingestion_status"] in {"green", "amber", "red"}

    def test_empty_db_does_not_500(self, client):
        response = client.get("/api/v1/dashboard", headers=AUTH)
        assert response.status_code == 200, response.text
        data = response.json()
        assert len(data["market_strip"]) == 7
        assert data["armed_alerts"] == 0
        assert data["candidates"]["top"] == []
        assert data["agent_brief"] is None
        assert data["portfolio"]["day_pnl"] is None
        assert data["ingestion_status"] in {"green", "amber", "red"}

    def test_unsupported_currency_422(self, client, dashboard_seed):
        response = client.get("/api/v1/dashboard", params={"currency": "GBP"}, headers=AUTH)
        assert response.status_code == 422


class TestHeatmap:
    def test_portfolio_mode_sizes_by_position_value(self, client, dashboard_seed):
        heatmap = client.get(
            "/api/v1/dashboard/heatmap",
            params={"mode": "portfolio", "timeframe": "1D", "currency": "USD"},
            headers=AUTH,
        ).json()
        assert heatmap["mode"] == "portfolio"
        [tile] = heatmap["tiles"]  # only AAPL is held
        assert tile["symbol"] == "AAPL"

        position = client.get(
            "/api/v1/portfolio/positions", params={"currency": "USD"}, headers=AUTH
        ).json()["positions"][0]
        assert tile["size"] == pytest.approx(position["value"])
        assert tile["pnl"] == pytest.approx(position["unrealized_pnl"])
        # change_pct is the return_1d column (0.012) rendered as a percent
        assert tile["change_pct"] == pytest.approx(1.2)
        assert tile["weight_pct"] == pytest.approx(100.0)

    def test_watchlist_mode_market_cap_with_fallback(self, client, dashboard_seed):
        heatmap = client.get(
            "/api/v1/dashboard/heatmap",
            params={"mode": "watchlist", "timeframe": "YTD"},
            headers=AUTH,
        ).json()
        by_symbol = {t["symbol"]: t for t in heatmap["tiles"]}
        assert set(by_symbol) == {"AAPL", "BTC"}
        assert by_symbol["AAPL"]["size"] == pytest.approx(3.0e12)  # market cap
        assert by_symbol["BTC"]["size"] == pytest.approx(1.0)  # no metrics -> fallback

    def test_market_mode_covers_active_universe(self, client, dashboard_seed):
        heatmap = client.get(
            "/api/v1/dashboard/heatmap",
            params={"mode": "market", "timeframe": "1W"},
            headers=AUTH,
        ).json()
        # every seeded asset is active and tracked
        assert {t["symbol"] for t in heatmap["tiles"]} >= {
            "AAPL", "SPY", "BTC", "ETH", "QQQ", "EURUSD", "USDMXN", "UUP",
        }

    def test_empty_portfolio_heatmap_is_empty_not_500(self, client, asset_ids):
        heatmap = client.get(
            "/api/v1/dashboard/heatmap", params={"mode": "portfolio"}, headers=AUTH
        )
        assert heatmap.status_code == 200
        assert heatmap.json()["tiles"] == []

    @pytest.mark.parametrize(
        "params",
        [{"mode": "bogus"}, {"timeframe": "5D"}, {"mode": "market", "timeframe": "10Y"}],
    )
    def test_invalid_mode_or_timeframe_422(self, client, params):
        response = client.get("/api/v1/dashboard/heatmap", params=params, headers=AUTH)
        assert response.status_code == 422
