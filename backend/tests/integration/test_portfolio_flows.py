"""Phase 5 integration: accounts / transactions / bank investments CRUD,
positions / performance / allocation reports against seeded bars + fx."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.db import session_scope
from app.ingestion.eod import upsert_candles
from app.ingestion.seed import seed_assets
from tests.integration.conftest import TEST_TOKEN

pytestmark = pytest.mark.integration

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture
def client():
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def asset_ids():
    seed_assets()
    from sqlalchemy import select

    from app.models import Asset

    with session_scope() as session:
        rows = session.execute(select(Asset.symbol, Asset.id)).all()
        return {symbol: asset_id for symbol, asset_id in rows}


def make_account(client, name="Bitso", type="exchange", **extra) -> dict:
    response = client.post(
        "/api/v1/accounts", json={"name": name, "type": type, **extra}, headers=AUTH
    )
    assert response.status_code == 201, response.text
    return response.json()


def post_txn(client, **body) -> dict:
    defaults = {"fees": 0, "currency": "USD", "ts": "2026-01-05T12:00:00Z"}
    response = client.post("/api/v1/transactions", json={**defaults, **body}, headers=AUTH)
    assert response.status_code == 201, response.text
    return response.json()


class TestAccounts:
    def test_crud_roundtrip(self, client):
        account = make_account(client, currency="MXN", type="bank", name="BBVA")
        assert account["currency"] == "MXN"

        response = client.get("/api/v1/accounts", headers=AUTH)
        assert [a["name"] for a in response.json()] == ["BBVA"]

        response = client.put(
            f"/api/v1/accounts/{account['id']}",
            json={"name": "BBVA Ahorro", "type": "bank", "currency": "MXN"},
            headers=AUTH,
        )
        assert response.status_code == 200
        assert response.json()["name"] == "BBVA Ahorro"

        response = client.patch(
            f"/api/v1/accounts/{account['id']}", json={"is_active": False}, headers=AUTH
        )
        assert response.json()["is_active"] is False

        assert client.delete(f"/api/v1/accounts/{account['id']}", headers=AUTH).status_code == 204
        assert client.get(f"/api/v1/accounts/{account['id']}", headers=AUTH).status_code == 404

    def test_duplicate_name_409(self, client):
        make_account(client)
        response = client.post(
            "/api/v1/accounts", json={"name": "Bitso", "type": "exchange"}, headers=AUTH
        )
        assert response.status_code == 409

    def test_cash_balances_embedded(self, client, asset_ids):
        account = make_account(client)
        post_txn(client, account_id=account["id"], type="deposit", quantity=10000, currency="MXN")
        post_txn(
            client,
            account_id=account["id"],
            type="buy",
            asset_id=asset_ids["BTC"],
            quantity=0.05,
            price=100000,
            fees=50,
            currency="MXN",
        )
        listed = client.get("/api/v1/accounts", headers=AUTH).json()[0]
        # 10000 - (0.05*100000 + 50) = 4950
        assert listed["cash_balances"] == [{"currency": "MXN", "amount": 4950.0}]
        assert listed["transactions_count"] == 2

    def test_delete_cascades_transactions(self, client, asset_ids):
        account = make_account(client)
        post_txn(client, account_id=account["id"], type="deposit", quantity=100)
        client.delete(f"/api/v1/accounts/{account['id']}", headers=AUTH)
        listed = client.get("/api/v1/transactions", headers=AUTH).json()
        assert listed["total"] == 0


class TestTransactions:
    def test_validation_errors(self, client, asset_ids):
        account = make_account(client)
        cases = [
            # unknown account
            ({"account_id": 999, "type": "deposit", "quantity": 1}, "account_id"),
            # buy without an asset
            ({"account_id": account["id"], "type": "buy", "quantity": 1, "price": 1}, "asset_id"),
            # unknown asset
            (
                {
                    "account_id": account["id"],
                    "type": "buy",
                    "asset_id": 999,
                    "quantity": 1,
                    "price": 1,
                },
                "asset_id",
            ),
            # buy without a price (silent zero basis)
            (
                {
                    "account_id": account["id"],
                    "type": "buy",
                    "asset_id": asset_ids["AAPL"],
                    "quantity": 1,
                },
                "price",
            ),
        ]
        for body, path in cases:
            response = client.post(
                "/api/v1/transactions",
                json={"ts": "2026-01-05T12:00:00Z", "currency": "USD", **body},
                headers=AUTH,
            )
            assert response.status_code == 422, (body, response.text)
            assert any(e["path"] == path for e in response.json()["detail"]["errors"]), (
                body,
                response.text,
            )

    def test_oversell_rejected(self, client, asset_ids):
        account = make_account(client)
        post_txn(
            client,
            account_id=account["id"],
            type="buy",
            asset_id=asset_ids["AAPL"],
            quantity=5,
            price=200,
            ts="2026-01-05T12:00:00Z",
        )
        response = client.post(
            "/api/v1/transactions",
            json={
                "account_id": account["id"],
                "type": "sell",
                "asset_id": asset_ids["AAPL"],
                "quantity": 8,
                "price": 210,
                "currency": "USD",
                "ts": "2026-02-05T12:00:00Z",
            },
            headers=AUTH,
        )
        assert response.status_code == 422
        assert "sells 8" in response.json()["detail"]["errors"][0]["error"]

    def test_lot_links_validated(self, client, asset_ids):
        account = make_account(client)
        buy = post_txn(
            client,
            account_id=account["id"],
            type="buy",
            asset_id=asset_ids["AAPL"],
            quantity=5,
            price=200,
            ts="2026-01-05T12:00:00Z",
        )
        # links must cover the whole sell quantity
        response = client.post(
            "/api/v1/transactions",
            json={
                "account_id": account["id"],
                "type": "sell",
                "asset_id": asset_ids["AAPL"],
                "quantity": 5,
                "price": 210,
                "currency": "USD",
                "ts": "2026-02-05T12:00:00Z",
                "lot_links": [{"buy_transaction_id": buy["id"], "quantity": 2}],
            },
            headers=AUTH,
        )
        assert response.status_code == 422
        # valid specific-ID sell
        response = client.post(
            "/api/v1/transactions",
            json={
                "account_id": account["id"],
                "type": "sell",
                "asset_id": asset_ids["AAPL"],
                "quantity": 2,
                "price": 210,
                "currency": "USD",
                "ts": "2026-02-05T12:00:00Z",
                "lot_links": [{"buy_transaction_id": buy["id"], "quantity": 2}],
            },
            headers=AUTH,
        )
        assert response.status_code == 201

    def test_edit_that_breaks_later_sell_409(self, client, asset_ids):
        account = make_account(client)
        buy = post_txn(
            client,
            account_id=account["id"],
            type="buy",
            asset_id=asset_ids["AAPL"],
            quantity=10,
            price=200,
            ts="2026-01-05T12:00:00Z",
        )
        post_txn(
            client,
            account_id=account["id"],
            type="sell",
            asset_id=asset_ids["AAPL"],
            quantity=8,
            price=210,
            ts="2026-02-05T12:00:00Z",
        )
        # shrinking the buy to 5 would orphan the sell of 8
        response = client.put(
            f"/api/v1/transactions/{buy['id']}",
            json={
                "account_id": account["id"],
                "type": "buy",
                "asset_id": asset_ids["AAPL"],
                "quantity": 5,
                "price": 200,
                "currency": "USD",
                "ts": "2026-01-05T12:00:00Z",
            },
            headers=AUTH,
        )
        assert response.status_code == 422  # strict replay catches it with the new row included
        # deleting the buy outright is a 409
        response = client.delete(f"/api/v1/transactions/{buy['id']}", headers=AUTH)
        assert response.status_code == 409
        assert "break a later sell" in response.json()["detail"]

    def test_filters_and_pagination(self, client, asset_ids):
        account = make_account(client)
        other = make_account(client, name="Ledger", type="wallet")
        for i in range(3):
            post_txn(
                client,
                account_id=account["id"],
                type="deposit",
                quantity=100 + i,
                ts=f"2026-01-0{i + 1}T12:00:00Z",
            )
        post_txn(
            client,
            account_id=other["id"],
            type="transfer_in",
            asset_id=asset_ids["BTC"],
            quantity=0.5,
            price=90000,
            ts="2026-01-04T12:00:00Z",
        )

        listed = client.get(
            "/api/v1/transactions", params={"account_id": account["id"]}, headers=AUTH
        ).json()
        assert listed["total"] == 3
        assert all(item["account_name"] == "Bitso" for item in listed["items"])

        listed = client.get(
            "/api/v1/transactions", params={"type": "transfer_in"}, headers=AUTH
        ).json()
        assert listed["total"] == 1
        assert listed["items"][0]["symbol"] == "BTC"

        page = client.get(
            "/api/v1/transactions", params={"limit": 2, "offset": 0}, headers=AUTH
        ).json()
        assert page["total"] == 4
        assert len(page["items"]) == 2
        # newest first
        assert page["items"][0]["ts"] > page["items"][1]["ts"]

    def test_time_window_filter(self, client):
        account = make_account(client)
        post_txn(client, account_id=account["id"], type="deposit", quantity=1,
                 ts="2026-01-01T12:00:00Z")
        post_txn(client, account_id=account["id"], type="deposit", quantity=2,
                 ts="2026-03-01T12:00:00Z")
        listed = client.get(
            "/api/v1/transactions",
            params={"start": "2026-02-01T00:00:00Z", "end": "2026-04-01T00:00:00Z"},
            headers=AUTH,
        ).json()
        assert listed["total"] == 1
        assert float(listed["items"][0]["quantity"]) == 2


class TestBankInvestments:
    def bank_account(self, client) -> dict:
        return make_account(client, name="BBVA", type="bank", currency="MXN")

    def test_lifecycle_and_computed_fields(self, client):
        account = self.bank_account(client)
        start = datetime.now(UTC).date().replace(day=1)
        response = client.post(
            "/api/v1/bank-investments",
            json={
                "account_id": account["id"],
                "name": "Pagaré 90d",
                "kind": "fixed_term",
                "principal": 100000,
                "currency": "MXN",
                "annual_rate": 0.10,
                "start_date": start.isoformat(),
                "term_days": 90,
            },
            headers=AUTH,
        )
        assert response.status_code == 201, response.text
        investment = response.json()
        assert investment["maturity_date"] is not None
        assert investment["projected_maturity_value"] == pytest.approx(
            100000 * (1 + 0.10 * 90 / 360), abs=0.01
        )
        assert investment["days_to_maturity"] is not None
        assert investment["effective_annual_rate"] == pytest.approx(0.10)
        assert investment["account_name"] == "BBVA"

        listed = client.get("/api/v1/bank-investments", headers=AUTH).json()
        assert len(listed) == 1

        response = client.put(
            f"/api/v1/bank-investments/{investment['id']}",
            json={
                "account_id": account["id"],
                "name": "Pagaré 90d",
                "kind": "fixed_term",
                "principal": 100000,
                "currency": "MXN",
                "annual_rate": 0.10,
                "start_date": start.isoformat(),
                "term_days": 90,
                "status": "closed",
            },
            headers=AUTH,
        )
        assert response.json()["status"] == "closed"

        assert (
            client.delete(f"/api/v1/bank-investments/{investment['id']}", headers=AUTH).status_code
            == 204
        )

    def test_tiered_demand_investment(self, client):
        account = self.bank_account(client)
        response = client.post(
            "/api/v1/bank-investments",
            json={
                "account_id": account["id"],
                "name": "Cuenta que rinde",
                "kind": "demand",
                "principal": 40000,
                "currency": "MXN",
                "annual_rate": 0.05,
                "rate_tiers": [
                    {"up_to": 25000, "annual_rate": 0.15},
                    {"up_to": None, "annual_rate": 0.05},
                ],
                "start_date": "2026-01-01",
            },
            headers=AUTH,
        )
        assert response.status_code == 201, response.text
        investment = response.json()
        assert investment["maturity_date"] is None
        assert investment["projected_maturity_value"] is None
        # (25000*0.15 + 15000*0.05)/40000
        assert investment["effective_annual_rate"] == pytest.approx(0.1125)

    def test_validation_errors(self, client):
        bank = self.bank_account(client)
        exchange = make_account(client, name="Bitso", type="exchange")
        base = {
            "name": "x",
            "kind": "fixed_term",
            "principal": 1000,
            "annual_rate": 0.1,
            "start_date": "2026-01-01",
        }
        cases = [
            ({**base, "account_id": exchange["id"], "term_days": 90}, "account_id"),
            ({**base, "account_id": bank["id"]}, "term_days"),  # fixed_term needs a term
            (
                {
                    **base,
                    "account_id": bank["id"],
                    "term_days": 90,
                    "rate_tiers": [
                        {"up_to": None, "annual_rate": 0.1},
                        {"up_to": 500, "annual_rate": 0.2},
                    ],
                },
                "rate_tiers.0.up_to",  # null tier must be last
            ),
            (
                {
                    **base,
                    "account_id": bank["id"],
                    "term_days": 90,
                    "rate_tiers": [
                        {"up_to": 500, "annual_rate": 0.1},
                        {"up_to": 400, "annual_rate": 0.2},
                    ],
                },
                "rate_tiers.1.up_to",  # descending tiers
            ),
        ]
        for body, path in cases:
            response = client.post("/api/v1/bank-investments", json=body, headers=AUTH)
            assert response.status_code == 422, (body, response.text)
            assert any(e["path"] == path for e in response.json()["detail"]["errors"]), (
                path,
                response.text,
            )


def seed_flat_bars(asset_ids: dict, days: int = 400) -> None:
    """AAPL drifts 200→240, USDMXN pinned at 20, SPY drifts 500→550."""
    end = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    with session_scope() as session:
        for symbol, start_price, end_price, volume in (
            ("AAPL", 200.0, 240.0, 1e6),
            ("SPY", 500.0, 550.0, 1e6),
            ("USDMXN", 20.0, 20.0, None),
        ):
            step = (end_price - start_price) / max(days - 1, 1)
            rows = [
                {
                    "asset_id": asset_ids[symbol],
                    "interval": "1d",
                    "ts": end - timedelta(days=days - i),
                    "open": start_price + step * i,
                    "high": start_price + step * i + 1,
                    "low": start_price + step * i - 1,
                    "close": round(start_price + step * i, 4),
                    "volume": volume,
                }
                for i in range(days)
            ]
            upsert_candles(session, rows)


def iso_days_ago(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT12:00:00Z")


@pytest.fixture
def funded_portfolio(client, asset_ids):
    """Bitso: 5000 USD deposited, 10 AAPL bought; BBVA: a 50k MXN pagaré."""
    seed_flat_bars(asset_ids)
    bitso = make_account(client, name="Bitso", type="exchange")
    bbva = make_account(client, name="BBVA", type="bank", currency="MXN")
    post_txn(client, account_id=bitso["id"], type="deposit", quantity=5000,
             ts=iso_days_ago(360))
    post_txn(client, account_id=bitso["id"], type="buy", asset_id=asset_ids["AAPL"],
             quantity=10, price=190, fees=10, ts=iso_days_ago(300))
    response = client.post(
        "/api/v1/bank-investments",
        json={
            "account_id": bbva["id"],
            "name": "Pagaré 180d",
            "kind": "fixed_term",
            "principal": 50000,
            "currency": "MXN",
            "annual_rate": 0.10,
            "start_date": (datetime.now(UTC) - timedelta(days=90)).date().isoformat(),
            "term_days": 180,
        },
        headers=AUTH,
    )
    assert response.status_code == 201, response.text
    return {"bitso": bitso, "bbva": bbva}


class TestPortfolioReports:
    def test_positions_in_usd(self, client, funded_portfolio):
        report = client.get(
            "/api/v1/portfolio/positions", params={"currency": "USD"}, headers=AUTH
        ).json()
        assert report["currency"] == "USD"
        [position] = report["positions"]
        assert position["symbol"] == "AAPL"
        assert position["quantity"] == pytest.approx(10)
        assert position["cost_basis"] == pytest.approx(1910.0)  # 10*190 + 10 fee
        assert position["last_price"] == pytest.approx(240.0, abs=1.0)
        assert position["unrealized_pnl"] == pytest.approx(
            position["value"] - 1910.0, abs=0.01
        )
        [cash] = report["cash"]
        assert cash["amount"] == pytest.approx(5000 - 1910)
        [bank] = report["bank_investments"]
        # 90 days of 10% on 50k MXN, ACT/360 = 1250 MXN accrued; at 20 MXN/USD
        assert bank["accrued_interest"] == pytest.approx(1250.0, abs=30.0)
        assert bank["value"] == pytest.approx(51250.0 / 20.0, abs=5.0)
        totals = report["totals"]
        assert totals["value"] == pytest.approx(
            position["value"] + cash["value"] + bank["value"], abs=0.05
        )

    def test_positions_in_mxn_converts_the_usd_side(self, client, funded_portfolio):
        usd = client.get(
            "/api/v1/portfolio/positions", params={"currency": "USD"}, headers=AUTH
        ).json()
        mxn = client.get(
            "/api/v1/portfolio/positions", params={"currency": "MXN"}, headers=AUTH
        ).json()
        # USDMXN pinned at 20 in the fixture
        assert mxn["totals"]["value"] == pytest.approx(usd["totals"]["value"] * 20.0, rel=1e-3)
        assert mxn["positions"][0]["market_value_native"] == pytest.approx(
            usd["positions"][0]["market_value_native"]
        )
        assert not [w for w in mxn["warnings"] if "rate" in w.get("warning", "")]

    def test_performance_report(self, client, funded_portfolio):
        report = client.get(
            "/api/v1/portfolio/performance",
            params={"period": "1y", "currency": "USD"},
            headers=AUTH,
        ).json()
        assert report["twr"] is not None
        assert report["irr"] is not None
        # AAPL rose and the pagaré accrues: both returns are positive
        assert report["twr"] > 0
        assert report["irr"] > 0
        assert report["benchmark"]["symbol"] == "SPY"
        assert report["indexed"][0][1] == pytest.approx(100.0)
        assert len(report["series"]) > 300
        # the two external flows: the deposit (bank principal is not a flow)
        assert len(report["flows"]) == 1

    def test_allocation_groups(self, client, funded_portfolio):
        by_class = client.get(
            "/api/v1/portfolio/allocation",
            params={"currency": "USD", "by": "asset_class"},
            headers=AUTH,
        ).json()
        keys = {group["key"] for group in by_class["groups"]}
        assert keys == {"stock", "cash & fixed income"}
        assert sum(g["weight"] for g in by_class["groups"]) == pytest.approx(1.0, abs=1e-4)

        by_currency = client.get(
            "/api/v1/portfolio/allocation",
            params={"currency": "MXN", "by": "currency"},
            headers=AUTH,
        ).json()
        assert {group["key"] for group in by_currency["groups"]} == {"USD", "MXN"}

        by_account = client.get(
            "/api/v1/portfolio/allocation", params={"by": "account"}, headers=AUTH
        ).json()
        assert {group["key"] for group in by_account["groups"]} == {"Bitso", "BBVA"}

    def test_unsupported_currency_422(self, client, funded_portfolio):
        response = client.get(
            "/api/v1/portfolio/positions", params={"currency": "GBP"}, headers=AUTH
        )
        assert response.status_code == 422

    def test_account_scope(self, client, funded_portfolio):
        report = client.get(
            "/api/v1/portfolio/positions",
            params={"currency": "USD", "account_id": funded_portfolio["bbva"]["id"]},
            headers=AUTH,
        ).json()
        assert report["positions"] == []
        assert len(report["bank_investments"]) == 1

    def test_missing_fx_degrades_with_warning(self, client, asset_ids):
        # EUR cash with no EURUSD bars seeded: unconverted, but the report renders
        account = make_account(client, name="EU", type="bank", currency="EUR")
        post_txn(client, account_id=account["id"], type="deposit", quantity=1000,
                 currency="EUR")
        report = client.get(
            "/api/v1/portfolio/positions", params={"currency": "USD"}, headers=AUTH
        ).json()
        assert report["cash"][0]["value"] == pytest.approx(1000.0)  # fallback 1:1
        assert any("EUR->USD" in w.get("warning", "") for w in report["warnings"])


BITSO_CSV = (
    "tid,date,type,major,minor,amount,rate,value,fee\n"
    "1001,2026-03-02 10:15,buy,btc,mxn,0.05,1800000,90000,225\n"
    "1002,2026-03-05 16:40,funding,,mxn,100000,,,0\n"
    "1003,2026-03-06 11:00,buy,doge,mxn,100,10,1000,1\n"  # unknown symbol -> row error
)


class TestCsvImport:
    def test_preview_then_commit_then_idempotent_recommit(self, client, asset_ids):
        account = make_account(client)

        preview = client.post(
            "/api/v1/portfolio/import/csv/preview", json={"content": BITSO_CSV}, headers=AUTH
        ).json()
        assert preview["preset"] == "bitso"
        assert preview["row_count"] == 3

        body = {
            "account_id": account["id"],
            "content": BITSO_CSV,
            "mapping": preview["suggested_mapping"],
        }
        first = client.post("/api/v1/portfolio/import/csv/commit", json=body, headers=AUTH).json()
        assert first["created"] == 2
        assert first["skipped_duplicates"] == 0
        assert len(first["errors"]) == 1
        assert "doge" in first["errors"][0]["error"]

        again = client.post("/api/v1/portfolio/import/csv/commit", json=body, headers=AUTH).json()
        assert again["created"] == 0
        assert again["skipped_duplicates"] == 2

        listed = client.get(
            "/api/v1/transactions", params={"account_id": account["id"]}, headers=AUTH
        ).json()
        assert listed["total"] == 2
        by_type = {item["type"]: item for item in listed["items"]}
        assert by_type["buy"]["symbol"] == "BTC"
        assert by_type["buy"]["currency"] == "MXN"
        assert by_type["deposit"]["external_id"] == "1002"

    def test_commit_validation(self, client, asset_ids):
        account = make_account(client)
        response = client.post(
            "/api/v1/portfolio/import/csv/commit",
            json={
                "account_id": 999,
                "content": BITSO_CSV,
                "mapping": {"ts": "date", "bogus": "x"},
            },
            headers=AUTH,
        )
        assert response.status_code == 422
        paths = {e["path"] for e in response.json()["detail"]["errors"]}
        assert paths == {"account_id", "mapping"}

        response = client.post(
            "/api/v1/portfolio/import/csv/commit",
            json={
                "account_id": account["id"],
                "content": BITSO_CSV,
                "mapping": {"ts": "date", "type": "type", "quantity": "amount"},
                "tz": "Mars/Olympus",
            },
            headers=AUTH,
        )
        assert response.status_code == 422
        assert response.json()["detail"]["errors"][0]["path"] == "tz"

    def test_preview_rejects_headerless_content(self, client):
        response = client.post(
            "/api/v1/portfolio/import/csv/preview", json={"content": "   \n"}, headers=AUTH
        )
        assert response.status_code == 422


class TestMaturities:
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

    def make_investment(self, client, *, start_days_ago: int, term_days: int,
                        auto_renew: bool = False, name: str = "Pagaré") -> dict:
        account_name = f"Banco {name}"
        account = make_account(client, name=account_name, type="bank", currency="MXN")
        response = client.post(
            "/api/v1/bank-investments",
            json={
                "account_id": account["id"],
                "name": name,
                "kind": "fixed_term",
                "principal": 100000,
                "currency": "MXN",
                "annual_rate": 0.12,
                "start_date": (
                    datetime.now(UTC) - timedelta(days=start_days_ago)
                ).date().isoformat(),
                "term_days": term_days,
                "auto_renew": auto_renew,
            },
            headers=AUTH,
        )
        assert response.status_code == 201, response.text
        return response.json()

    def test_reminder_sent_once(self, client, email_env, outbox):
        from app.portfolio.maturities import run_maturity_check

        # matures in 3 days (inside the 7-day reminder window)
        investment = self.make_investment(client, start_days_ago=87, term_days=90)
        assert run_maturity_check() == 1
        assert len(outbox.instances) == 1
        message = outbox.instances[0].sent[0]
        assert "matures in 3 day(s)" in message["Subject"]
        assert "Pagaré" in message.get_content()

        # second run: deduped by (investment, maturity_date)
        assert run_maturity_check() == 0
        assert len(outbox.instances) == 1

        # audit row exists
        from sqlalchemy import select

        from app.models import Notification

        with session_scope() as session:
            row = session.scalars(select(Notification)).one()
            assert row.kind == "maturity"
            assert row.meta["investment_id"] == investment["id"]

    def test_matured_without_auto_renew_flips_status(self, client, email_env, outbox):
        from app.portfolio.maturities import run_maturity_check

        investment = self.make_investment(client, start_days_ago=100, term_days=90)
        run_maturity_check()
        got = client.get(f"/api/v1/bank-investments/{investment['id']}", headers=AUTH).json()
        assert got["status"] == "matured"
        # a matured investment's value stays frozen at maturity value
        assert got["current_value"] == pytest.approx(100000 * (1 + 0.12 * 90 / 360), abs=0.01)

    def test_auto_renew_capitalizes_and_rolls(self, client, email_env, outbox):
        from app.portfolio.maturities import run_maturity_check

        investment = self.make_investment(
            client, start_days_ago=100, term_days=90, auto_renew=True, name="Renovable"
        )
        run_maturity_check()
        got = client.get(f"/api/v1/bank-investments/{investment['id']}", headers=AUTH).json()
        assert got["status"] == "active"
        # principal capitalized: 100000 * (1 + 0.12*90/360) = 103000
        assert got["principal"] == pytest.approx(103000.0, abs=0.01)
        assert got["start_date"] == investment["maturity_date"]
        assert "auto-renewed" in got["note"]
        # renewed maturity is 80 days out -> outside the window, no reminder
        assert len(outbox.instances) == 0

    def test_unconfigured_channels_send_nothing(self, client):
        from app.portfolio.maturities import run_maturity_check

        self.make_investment(client, start_days_ago=87, term_days=90)
        assert run_maturity_check() == 0  # no channel configured, no crash
