"""End-to-end price-alert evaluator against the real plutus_test DB + test Redis.

Seeds an armed rule, drives the quote cache (`quote:last:<SYMBOL>`, TTL 120s) with
setex, and asserts crossing-edge behavior: baseline-then-fire, one-shot (no
re-fire), re-arm re-baselines, disabled rules are skipped, and a missing quote is
counted stale without touching the rule.

The integration conftest pins alert channels BLANK, so the real deliver() logs a
"not configured" warning and returns [] WITHOUT writing any Notification rows.
Tests therefore assert the status flip directly and monkeypatch deliver() to
capture the crossing payload (kind='price_alert', subject, meta) — the same
capture pattern the discovery-notify tests use. One test leaves deliver() real to
prove the blank-channel path still flips the rule and writes no rows.

Redis db 1 (the test DB) is flushed between tests by the autouse clean_state
fixture; each test also uses a dedicated symbol and deletes its own quote keys so
a shared dev Redis never leaks state into or out of these tests.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.alerts import evaluate as evaluate_module
from app.alerts.evaluate import evaluate_alerts
from app.core.db import session_scope
from app.models import AlertRule, Asset, Notification
from app.providers.registry import _shared_redis
from tests.integration.conftest import TEST_TOKEN

pytestmark = pytest.mark.integration

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}
SYMBOL = "ZALERT"  # dedicated, cannot collide with any real streamer symbol


@pytest.fixture
def client():
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def redis():
    return _shared_redis()


def _armed_rule(condition="above", threshold=120000, symbol=SYMBOL, status="armed"):
    """Create a crypto asset + a rule for it; returns (asset_id, alert_id)."""
    with session_scope() as session:
        asset = Asset(symbol=symbol, name=f"{symbol} Coin", asset_class="crypto", currency="USD")
        session.add(asset)
        session.flush()
        rule = AlertRule(
            asset_id=asset.id, condition=condition, threshold=threshold, status=status
        )
        session.add(rule)
        session.flush()
        return asset.id, rule.id


def _set_quote(redis, price, symbol=SYMBOL):
    tick = {
        "symbol": symbol,
        "price": price,
        "change_pct": 0.0,
        "ts": "2026-07-06T12:00:00+00:00",
        "source": "binance",
    }
    redis.setex(f"quote:last:{symbol.upper()}", 120, json.dumps(tick))


def _clear_quote(redis, symbol=SYMBOL):
    redis.delete(f"quote:last:{symbol.upper()}")


def _run(redis):
    with session_scope() as session:
        return evaluate_alerts(session, redis)


def _reload(alert_id):
    with session_scope() as session:
        rule = session.get(AlertRule, alert_id)
        return {
            "status": rule.status,
            "last_price": rule.last_price,
            "last_triggered_at": rule.last_triggered_at,
        }


class TestBaselineThenFire:
    def test_first_below_baselines_then_upcross_fires_once(self, redis, monkeypatch):
        captured = []

        def fake_deliver(session, kind, subject, body, meta):
            captured.append({"kind": kind, "subject": subject, "body": body, "meta": meta})
            return []

        monkeypatch.setattr(evaluate_module, "deliver", fake_deliver)
        asset_id, alert_id = _armed_rule("above", 120000)

        # first observation below the threshold -> baseline, never fires
        _set_quote(redis, 100000)
        summary = _run(redis)
        assert summary == {"evaluated": 1, "fired": 0, "stale": 0}
        state = _reload(alert_id)
        assert state["status"] == "armed"
        assert float(state["last_price"]) == 100000.0
        assert state["last_triggered_at"] is None
        assert captured == []

        # quote crosses strictly above -> fires exactly once
        _set_quote(redis, 125000)
        summary = _run(redis)
        assert summary == {"evaluated": 1, "fired": 1, "stale": 0}
        state = _reload(alert_id)
        assert state["status"] == "triggered"
        assert float(state["last_price"]) == 125000.0
        assert state["last_triggered_at"] is not None

        assert len(captured) == 1
        call = captured[0]
        assert call["kind"] == "price_alert"
        assert call["subject"] == f"Price alert: {SYMBOL} above 120000"
        assert call["meta"] == {
            "alert_id": alert_id,
            "asset_id": asset_id,
            "symbol": SYMBOL,
            "condition": "above",
            "threshold": 120000.0,
            "price": 125000.0,
        }

        # one-shot: a still-higher quote does not re-fire (rule is 'triggered',
        # no longer selected as armed)
        _set_quote(redis, 130000)
        summary = _run(redis)
        assert summary == {"evaluated": 0, "fired": 0, "stale": 0}
        assert len(captured) == 1
        assert _reload(alert_id)["status"] == "triggered"

        _clear_quote(redis)

    def test_below_condition_downcross_fires(self, redis, monkeypatch):
        captured = []
        monkeypatch.setattr(
            evaluate_module,
            "deliver",
            lambda *a, **k: captured.append(a) or [],
        )
        _, alert_id = _armed_rule("below", 100)

        _set_quote(redis, 110)  # baseline above threshold
        assert _run(redis)["fired"] == 0
        _set_quote(redis, 90)  # crosses strictly below
        assert _run(redis)["fired"] == 1
        assert _reload(alert_id)["status"] == "triggered"
        assert len(captured) == 1

        _clear_quote(redis)


class TestReArm:
    def test_rearm_clears_last_price_and_next_eval_rebaselines(self, redis, client, monkeypatch):
        captured = []
        monkeypatch.setattr(
            evaluate_module, "deliver", lambda *a, **k: captured.append(a) or []
        )
        _, alert_id = _armed_rule("above", 120000)

        _set_quote(redis, 100000)
        _run(redis)
        _set_quote(redis, 125000)
        assert _run(redis)["fired"] == 1  # fired -> triggered

        # re-arm via the API: apply_patch clears last_price + last_triggered_at
        resp = client.patch(
            f"/api/v1/alerts/{alert_id}", headers=AUTH, json={"status": "armed"}
        )
        assert resp.status_code == 200
        state = _reload(alert_id)
        assert state["status"] == "armed"
        assert state["last_price"] is None

        # next eval with a quote ALREADY above the threshold must re-baseline,
        # not re-fire (last_price is None -> first observation)
        _set_quote(redis, 135000)
        summary = _run(redis)
        assert summary == {"evaluated": 1, "fired": 0, "stale": 0}
        state = _reload(alert_id)
        assert state["status"] == "armed"
        assert float(state["last_price"]) == 135000.0
        assert len(captured) == 1  # only the pre-rearm fire

        _clear_quote(redis)


class TestSkips:
    def test_disabled_rule_is_never_evaluated(self, redis, monkeypatch):
        monkeypatch.setattr(evaluate_module, "deliver", lambda *a, **k: [])
        _, alert_id = _armed_rule("above", 120000, status="disabled")

        _set_quote(redis, 200000)  # would cross if it were armed
        summary = _run(redis)
        assert summary == {"evaluated": 0, "fired": 0, "stale": 0}
        state = _reload(alert_id)
        assert state["status"] == "disabled"
        assert state["last_price"] is None  # untouched

        _clear_quote(redis)

    def test_missing_quote_counts_stale_and_leaves_rule_untouched(self, redis, monkeypatch):
        monkeypatch.setattr(evaluate_module, "deliver", lambda *a, **k: [])
        _, alert_id = _armed_rule("above", 120000)

        _clear_quote(redis)  # no quote:last key -> expired TTL / streamer down
        summary = _run(redis)
        assert summary == {"evaluated": 0, "fired": 0, "stale": 1}
        state = _reload(alert_id)
        assert state["status"] == "armed"
        assert state["last_price"] is None  # crossing memory preserved


class TestBlankChannels:
    def test_fire_with_real_blank_channel_deliver_flips_and_writes_no_rows(self, redis):
        # deliver() is NOT patched here: with channels blank it returns [] and
        # writes no Notification rows, yet the status flip must still happen.
        _, alert_id = _armed_rule("above", 120000)

        _set_quote(redis, 100000)
        _run(redis)
        _set_quote(redis, 125000)
        summary = _run(redis)
        assert summary["fired"] == 1
        assert _reload(alert_id)["status"] == "triggered"

        with session_scope() as session:
            rows = session.query(Notification).filter(
                Notification.kind == "price_alert"
            ).all()
        assert rows == []  # blank channels -> no notification rows persisted

        _clear_quote(redis)
