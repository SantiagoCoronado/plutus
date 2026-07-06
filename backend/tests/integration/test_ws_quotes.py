"""WS /ws/quotes: token gate + last-quote replay.

Deterministic by design — we exercise the replay path (write quote:last:BTC
straight into the test Redis, then subscribe and read the frame back). Live
pub/sub forwarding is covered structurally elsewhere and left out here to avoid
timing flakiness. The handler's async Redis and the test's sync Redis both point
at REDIS_URL (redis://localhost:6379/1).
"""

import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.quotes.publisher import _last_key
from tests.integration.conftest import TEST_TOKEN

pytestmark = pytest.mark.integration


@pytest.fixture
def client():
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def test_bad_token_closes_4401(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/quotes?token=wrong") as ws:
            ws.receive_text()
    assert exc.value.code == 4401


def test_missing_token_closes_4401(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/quotes") as ws:
            ws.receive_text()
    assert exc.value.code == 4401


def test_subscribe_replays_last_quote(client):
    from app.providers.registry import _shared_redis

    tick = {
        "symbol": "BTC",
        "price": 51234.5,
        "change_pct": 1.75,
        "ts": "2026-07-06T12:00:00+00:00",
        "source": "binance",
    }
    _shared_redis().setex(_last_key("BTC"), 120, json.dumps(tick))

    with client.websocket_connect(f"/ws/quotes?token={TEST_TOKEN}") as ws:
        ws.send_json({"action": "subscribe", "symbols": ["BTC"]})
        frame = ws.receive_json()
        assert frame["type"] == "tick"
        assert frame["symbol"] == "BTC"
        assert frame["price"] == 51234.5
        assert frame["source"] == "binance"
