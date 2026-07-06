"""Phase 7 M2 integration: Bitso credential storage (ciphertext + masking),
the Test-connection endpoint, a full read-only sync against respx-mocked Bitso
(trades + fundings + withdrawals) with idempotent re-sync, and the sync trigger
route enqueuing Celery.

The integration conftest pins FERNET_KEY="" — tests that touch encryption set a
real key via monkeypatch + get_settings.cache_clear (see test_agent_settings)."""

import types

import httpx
import pytest
import respx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.core.config import get_settings
from app.core.db import SessionLocal, session_scope
from app.exchanges.settings_store import set_exchange_setting
from app.ingestion.seed import seed_assets
from app.models import Account, ExchangeLink, ExchangeSyncRun, Transaction
from tests.integration.conftest import TEST_TOKEN

pytestmark = pytest.mark.integration

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}
BITSO = "https://api.bitso.com"

TRADES = [
    {
        "tid": 100, "book": "btc_mxn", "side": "buy",
        "major": "0.5", "minor": "-450000", "price": "900000",
        "fees_amount": "-50", "fees_currency": "mxn",
        "created_at": "2026-05-01T10:00:00+00:00",
    },
    {
        "tid": 101, "book": "btc_mxn", "side": "sell",
        "major": "-0.3", "minor": "285000", "price": "950000",
        "fees_amount": "-40", "fees_currency": "mxn",
        "created_at": "2026-05-02T11:00:00+00:00",
    },
]
FUNDINGS = [
    {"fid": "f1", "currency": "mxn", "amount": "5000", "status": "complete",
     "method": "spei", "created_at": "2026-04-01T09:00:00+00:00"},
    {"fid": "f2", "currency": "btc", "amount": "0.1", "status": "complete",
     "method": "crypto", "created_at": "2026-04-05T09:00:00+00:00"},
    {"fid": "f3", "currency": "mxn", "amount": "100", "status": "pending",
     "method": "spei", "created_at": "2026-04-06T09:00:00+00:00"},
]
WITHDRAWALS = [
    {"wid": "w1", "currency": "mxn", "amount": "2000", "status": "complete",
     "method": "spei", "created_at": "2026-06-01T09:00:00+00:00"},
    {"wid": "w2", "currency": "btc", "amount": "0.05", "status": "complete",
     "method": "crypto", "created_at": "2026-06-02T09:00:00+00:00"},
]


@pytest.fixture
def fernet_env(monkeypatch):
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client(fernet_env):
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def _wrap(payload):
    return httpx.Response(200, json={"success": True, "payload": payload})


def _create_exchange_account(name="Bitso MX", provider="bitso", account_type="exchange") -> int:
    with session_scope() as session:
        account = Account(name=name, type=account_type, provider=provider, currency="MXN")
        session.add(account)
        session.flush()
        return account.id


def _store_creds():
    with session_scope() as session:
        set_exchange_setting(session, "bitso_api_key", "test-key")
        set_exchange_setting(session, "bitso_api_secret", "test-secret")


def _mock_bitso(respx_mock):
    respx_mock.get("/v3/user_trades").mock(return_value=_wrap(TRADES))
    respx_mock.get("/v3/fundings").mock(return_value=_wrap(FUNDINGS))
    respx_mock.get("/v3/withdrawals").mock(return_value=_wrap(WITHDRAWALS))


class TestCredentials:
    def test_status_unconfigured(self, client):
        body = client.get("/api/v1/exchanges/status", headers=AUTH).json()
        assert body["configured"] is False
        assert body["keys"]["bitso_api_key"] is None
        assert body["fernet_ready"] is True
        assert body["accounts"] == []

    def test_put_keys_stores_ciphertext_and_masks(self, client):
        resp = client.put(
            "/api/v1/exchanges/bitso/keys",
            json={"api_key": "plainkey-abcd1234", "api_secret": "plainsecret-wxyz9876"},
            headers=AUTH,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["configured"] is True
        masked = body["keys"]["bitso_api_key"]
        assert masked is not None and "plainkey-abcd1234" not in masked

        with session_scope() as session:
            rows = session.execute(
                text(
                    "SELECT value, is_secret FROM app_settings "
                    "WHERE key IN ('bitso_api_key','bitso_api_secret')"
                )
            ).all()
        assert len(rows) == 2
        for row in rows:
            assert row.is_secret is True
            assert "plainkey-abcd1234" not in row.value
            assert "plainsecret-wxyz9876" not in row.value

    def test_keys_without_fernet_422(self, client, monkeypatch):
        monkeypatch.setenv("FERNET_KEY", "")
        get_settings.cache_clear()
        resp = client.put(
            "/api/v1/exchanges/bitso/keys", json={"api_key": "x"}, headers=AUTH
        )
        assert resp.status_code == 422
        assert "FERNET_KEY" in resp.text


class TestTestConnection:
    def test_missing_creds_422(self, client):
        resp = client.post("/api/v1/exchanges/bitso/test", headers=AUTH)
        assert resp.status_code == 422

    @respx.mock(base_url=BITSO)
    def test_ok(self, respx_mock, client):
        _store_creds()
        respx_mock.get("/v3/balance").mock(
            return_value=_wrap(
                {"balances": [
                    {"currency": "btc", "total": "0.5", "available": "0.5", "locked": "0"},
                    {"currency": "mxn", "total": "100", "available": "100", "locked": "0"},
                ]}
            )
        )
        body = client.post("/api/v1/exchanges/bitso/test", headers=AUTH).json()
        assert body["ok"] is True
        assert body["currencies"] == 2

    @respx.mock(base_url=BITSO)
    def test_bad_auth_is_friendly(self, respx_mock, client):
        _store_creds()
        respx_mock.get("/v3/balance").mock(
            return_value=httpx.Response(401, json={"success": False})
        )
        body = client.post("/api/v1/exchanges/bitso/test", headers=AUTH).json()
        assert body["ok"] is False
        assert "credentials" in body["error"].lower()


class TestSyncRoute:
    def test_enqueues_celery_202(self, client, monkeypatch):
        account_id = _create_exchange_account()
        import worker.tasks as tasks

        monkeypatch.setattr(
            tasks.sync_exchange, "delay", lambda aid: types.SimpleNamespace(id="task-xyz")
        )
        resp = client.post(f"/api/v1/exchanges/{account_id}/sync", headers=AUTH)
        assert resp.status_code == 202, resp.text
        assert resp.json()["task_id"] == "task-xyz"

    def test_rejects_non_exchange_account(self, client):
        account_id = _create_exchange_account(name="Cash", provider=None, account_type="manual")
        resp = client.post(f"/api/v1/exchanges/{account_id}/sync", headers=AUTH)
        assert resp.status_code == 422

    def test_unknown_account_404(self, client):
        resp = client.post("/api/v1/exchanges/99999/sync", headers=AUTH)
        assert resp.status_code == 404


class TestFullSync:
    @respx.mock(base_url=BITSO)
    def test_sync_creates_transactions_and_is_idempotent(self, respx_mock, fernet_env):
        seeded = {symbol: asset_id for asset_id, symbol in seed_assets()}
        btc_id = seeded["BTC"]
        account_id = _create_exchange_account()
        _store_creds()
        _mock_bitso(respx_mock)

        from app.exchanges.sync import sync_bitso_account

        run_id = sync_bitso_account(SessionLocal, account_id)

        with session_scope() as session:
            txns = session.scalars(
                select(Transaction).where(Transaction.account_id == account_id)
            ).all()
            assert len(txns) == 6
            assert {t.type for t in txns} == {
                "buy", "sell", "deposit", "transfer_in", "withdrawal", "transfer_out"
            }
            buy = next(t for t in txns if t.type == "buy")
            assert buy.asset_id == btc_id
            assert buy.external_id == "100"
            assert buy.currency == "MXN"
            assert float(buy.quantity) == pytest.approx(0.5)
            deposit = next(t for t in txns if t.type == "deposit")
            assert deposit.asset_id is None
            assert float(deposit.quantity) == pytest.approx(5000.0)
            transfer_in = next(t for t in txns if t.type == "transfer_in")
            assert transfer_in.asset_id == btc_id
            assert transfer_in.external_id == "f2"

            run = session.get(ExchangeSyncRun, run_id)
            assert run.status == "success"
            assert run.trades_created == 6
            assert run.trades_skipped == 0

            link = session.scalar(
                select(ExchangeLink).where(ExchangeLink.account_id == account_id)
            )
            assert link.last_trade_tid == "101"
            assert link.last_funding_id == "f3"
            assert link.last_withdrawal_id == "w2"
            assert link.last_status == "success"
            assert link.last_synced_at is not None

        # re-sync against the same mocked pages must create nothing
        run2_id = sync_bitso_account(SessionLocal, account_id)
        with session_scope() as session:
            txns = session.scalars(
                select(Transaction).where(Transaction.account_id == account_id)
            ).all()
            assert len(txns) == 6
            run2 = session.get(ExchangeSyncRun, run2_id)
            assert run2.status == "success"
            assert run2.trades_created == 0
            assert run2.trades_skipped == 6

    @respx.mock(base_url=BITSO)
    def test_no_credentials_fails_the_run(self, respx_mock, fernet_env):
        seed_assets()
        account_id = _create_exchange_account()
        # no creds stored
        from app.exchanges.sync import sync_bitso_account

        run_id = sync_bitso_account(SessionLocal, account_id)
        with session_scope() as session:
            run = session.get(ExchangeSyncRun, run_id)
            assert run.status == "failed"
            assert "credentials" in run.details["error"].lower()

    @respx.mock(base_url=BITSO)
    def test_status_reports_last_run(self, respx_mock, client):
        seed_assets()
        account_id = _create_exchange_account()
        client.put(
            "/api/v1/exchanges/bitso/keys",
            json={"api_key": "test-key", "api_secret": "test-secret"},
            headers=AUTH,
        )
        _mock_bitso(respx_mock)
        from app.exchanges.sync import sync_bitso_account

        sync_bitso_account(SessionLocal, account_id)

        body = client.get("/api/v1/exchanges/status", headers=AUTH).json()
        assert body["configured"] is True
        account = next(a for a in body["accounts"] if a["account_id"] == account_id)
        assert account["last_status"] == "success"
        assert account["last_run"]["trades_created"] == 6
        assert account["provider"] == "bitso"
