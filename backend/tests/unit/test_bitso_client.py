"""Bitso connector: HMAC signing vector, the structural read-only guarantee,
response unwrapping, and query/parse correctness under a mocked transport."""

import hashlib
import hmac
import re
from pathlib import Path

import httpx
import pytest
import respx

from app.exchanges import bitso as bitso_module
from app.exchanges.base import ExchangeError
from app.exchanges.bitso import BASE_URL, BitsoClient
from app.providers.base import ProviderAuthError, RateLimit
from app.providers.http import RateLimitedClient

WIDE_OPEN = RateLimit(capacity=1000, refill_amount=1000, refill_period_s=1)


def _client(fake_redis, fake_clock, *, nonce_start=1) -> BitsoClient:
    http = RateLimitedClient(
        "bitso", BASE_URL, fake_redis, WIDE_OPEN, clock=fake_clock, sleep=fake_clock.sleep
    )
    return BitsoClient("test-key", "test-secret", http, nonce_factory=lambda: nonce_start)


class TestSigning:
    def test_signature_vector(self):
        # fixed key/secret/nonce/path → an exact hex signature (recomputed independently)
        key, secret, nonce = "test-key", "test-secret", 1500000000000
        path = "/v3/balance"
        client = BitsoClient(key, secret, client=None, nonce_factory=lambda: nonce)

        header = client._auth_header("GET", path)

        message = f"{nonce}GET{path}"
        expected = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
        assert header == f"Bitso {key}:{nonce}:{expected}"

    def test_signature_covers_query_string(self):
        client = BitsoClient("k", "s", client=None, nonce_factory=lambda: 10)
        request_path = "/v3/user_trades?marker=99&sort=asc&limit=100"
        header = client._auth_header("GET", request_path)
        expected = hmac.new(
            b"s", f"10GET{request_path}".encode(), hashlib.sha256
        ).hexdigest()
        assert header.endswith(expected)

    def test_nonce_strictly_increases(self):
        client = BitsoClient("k", "s", client=None, nonce_factory=lambda: 5)
        first = int(client._auth_header("GET", "/v3/balance").split(":")[1])
        second = int(client._auth_header("GET", "/v3/balance").split(":")[1])
        assert second > first


class TestReadOnlyStructure:
    def test_no_write_verbs_exist(self):
        for verb in ("_post", "_put", "_delete", "post", "put", "delete"):
            assert not hasattr(BitsoClient, verb), f"BitsoClient must not expose {verb}"

    def test_source_references_no_order_paths(self):
        src = Path(bitso_module.__file__).read_text()
        for needle in ("/orders", "place_order", "/v3/orders", "cancel_order"):
            assert needle not in src


class TestRequests:
    @respx.mock
    def test_fetch_trades_sends_marker_and_parses(self, fake_redis, fake_clock):
        route = respx.get(url__regex=re.escape(BASE_URL) + r"/v3/user_trades").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "payload": [
                        {
                            "tid": 51756,
                            "book": "btc_mxn",
                            "side": "sell",
                            "major": "-0.25232073",
                            "minor": "1014.10",
                            "price": "4019.51",
                            "fees_amount": "-10.24",
                            "fees_currency": "mxn",
                            "created_at": "2026-04-08T17:52:31+00:00",
                        }
                    ],
                },
            )
        )
        client = _client(fake_redis, fake_clock)

        trades = client.fetch_trades(since_tid="99")

        sent = str(route.calls.last.request.url)
        assert "marker=99" in sent and "sort=asc" in sent and "limit=100" in sent
        assert route.calls.last.request.headers["Authorization"].startswith("Bitso test-key:")
        assert len(trades) == 1
        trade = trades[0]
        assert trade.tid == "51756"
        assert trade.book == "btc_mxn"
        assert trade.side == "sell"
        assert trade.major == pytest.approx(-0.25232073)
        assert trade.price == pytest.approx(4019.51)
        assert trade.fees_amount == pytest.approx(-10.24)

    @respx.mock
    def test_fetch_balances_unwraps_payload(self, fake_redis, fake_clock):
        respx.get(url__regex=re.escape(BASE_URL) + r"/v3/balance").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "payload": {
                        "balances": [
                            {"currency": "btc", "total": "0.5",
                             "available": "0.4", "locked": "0.1"},
                            {"currency": "mxn", "total": "100",
                             "available": "100", "locked": "0"},
                        ]
                    },
                },
            )
        )
        client = _client(fake_redis, fake_clock)
        balances = client.fetch_balances()
        assert [b.currency for b in balances] == ["BTC", "MXN"]
        assert balances[0].available == pytest.approx(0.4)

    @respx.mock
    def test_non_success_payload_raises_exchange_error(self, fake_redis, fake_clock):
        respx.get(url__regex=re.escape(BASE_URL) + r"/v3/balance").mock(
            return_value=httpx.Response(
                200, json={"success": False, "error": {"message": "invalid nonce"}}
            )
        )
        client = _client(fake_redis, fake_clock)
        with pytest.raises(ExchangeError, match="invalid nonce"):
            client.fetch_balances()

    @respx.mock
    def test_http_401_raises_auth_error(self, fake_redis, fake_clock):
        respx.get(url__regex=re.escape(BASE_URL) + r"/v3/balance").mock(
            return_value=httpx.Response(401, json={"success": False})
        )
        client = _client(fake_redis, fake_clock)
        with pytest.raises(ProviderAuthError):
            client.fetch_balances()
