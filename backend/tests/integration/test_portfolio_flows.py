"""Phase 5 integration: accounts / transactions / bank investments CRUD."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.core.db import session_scope
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
