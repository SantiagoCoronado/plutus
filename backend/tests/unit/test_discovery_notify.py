"""Alert composition + transports (SMTP monkeypatched, Telegram via respx)."""

import smtplib

import httpx
import pytest
import respx

from app.core.config import get_settings
from app.discovery.notify import (
    candidate_line,
    configured_channels,
    send_email,
    send_telegram,
)
from app.models import Candidate


@pytest.fixture
def alert_env(monkeypatch):
    def set_env(**overrides):
        defaults = {
            "SMTP_HOST": "",
            "SMTP_PORT": "587",
            "SMTP_USER": "",
            "SMTP_PASS": "",
            "ALERT_EMAIL_TO": "",
            "ALERT_EMAIL_FROM": "",
            "TELEGRAM_BOT_TOKEN": "",
            "TELEGRAM_CHAT_ID": "",
        }
        for key, value in {**defaults, **overrides}.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()

    yield set_env
    get_settings.cache_clear()


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.tls_started = False
        self.logins: list[tuple[str, str]] = []
        self.sent: list = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self):
        self.tls_started = True

    def login(self, user, password):
        self.logins.append((user, password))

    def send_message(self, message):
        self.sent.append(message)


@pytest.fixture(autouse=True)
def reset_fake_smtp():
    FakeSMTP.instances = []


def test_configured_channels(alert_env):
    alert_env()
    assert configured_channels() == []
    alert_env(SMTP_HOST="smtp.test", ALERT_EMAIL_TO="me@test.com")
    assert configured_channels() == ["email"]
    alert_env(TELEGRAM_BOT_TOKEN="token", TELEGRAM_CHAT_ID="42")
    assert configured_channels() == ["telegram"]
    alert_env(
        SMTP_HOST="smtp.test",
        ALERT_EMAIL_TO="me@test.com",
        TELEGRAM_BOT_TOKEN="token",
        TELEGRAM_CHAT_ID="42",
    )
    assert configured_channels() == ["email", "telegram"]


def test_send_email_starttls_path(alert_env, monkeypatch):
    alert_env(
        SMTP_HOST="smtp.test",
        SMTP_USER="bot@test.com",
        SMTP_PASS="secret",
        ALERT_EMAIL_TO="me@test.com",
    )
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    send_email("Subject line", "Body text")

    smtp = FakeSMTP.instances[0]
    assert smtp.host == "smtp.test" and smtp.port == 587
    assert smtp.tls_started
    assert smtp.logins == [("bot@test.com", "secret")]
    message = smtp.sent[0]
    assert message["Subject"] == "Subject line"
    assert message["To"] == "me@test.com"
    assert message["From"] == "bot@test.com"  # falls back to SMTP_USER
    assert "Body text" in message.get_content()


def test_send_email_implicit_tls_on_port_465(alert_env, monkeypatch):
    alert_env(SMTP_HOST="smtp.test", SMTP_PORT="465", ALERT_EMAIL_TO="me@test.com")
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    send_email("s", "b")
    smtp = FakeSMTP.instances[0]
    assert smtp.port == 465
    assert not smtp.tls_started  # implicit TLS, no STARTTLS call
    assert smtp.logins == []  # no user configured -> no login
    assert len(smtp.sent) == 1


@respx.mock
def test_send_telegram_posts_plain_text(alert_env):
    alert_env(TELEGRAM_BOT_TOKEN="token123", TELEGRAM_CHAT_ID="42")
    route = respx.post("https://api.telegram.org/bottoken123/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    send_telegram("Subject", "Body")
    assert route.called
    import json

    payload = json.loads(route.calls[0].request.content)
    assert payload["chat_id"] == "42"
    assert payload["text"] == "Subject\n\nBody"


@respx.mock
def test_send_telegram_raises_on_http_error(alert_env):
    alert_env(TELEGRAM_BOT_TOKEN="token123", TELEGRAM_CHAT_ID="42")
    respx.post("https://api.telegram.org/bottoken123/sendMessage").mock(
        return_value=httpx.Response(500)
    )
    with pytest.raises(httpx.HTTPStatusError):
        send_telegram("s", "b")


def make_candidate(**overrides) -> Candidate:
    fields = {
        "score": 84.2,
        "signals": [
            {"key": "crypto_drawdown", "label": "Far below the all-time high",
             "triggered": True},
            {"key": "volume_anomaly", "label": "Unusual volume", "triggered": True},
            {"key": "rsi_extreme", "label": "Oversold (RSI)", "triggered": False},
        ],
        "context": {
            "history_check": {
                "crypto_drawdown": {
                    "n_triggers": 13,
                    "fwd": {"20d": {"n": 13, "median": 0.031, "win_rate": 0.62}},
                }
            }
        },
    }
    fields.update(overrides)
    return Candidate(**fields)


def test_candidate_line_includes_triggered_labels_and_history():
    line = candidate_line("BTC", make_candidate())
    assert line == (
        "BTC — score 84 — Far below the all-time high, Unusual volume — "
        "after 13 past signals: +3.1% median 20-day move, 62% win rate"
    )


def test_candidate_line_without_history_check():
    line = candidate_line("ETH", make_candidate(context={}))
    assert line == "ETH — score 84 — Far below the all-time high, Unusual volume"
