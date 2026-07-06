"""Integration fixtures: real compose db (localhost:5433) + redis (db 1), mocked provider HTTP.

Environment is pinned at import time — before any app module creates the lazy engine.
Keep app.core.db imports out of unit-test module scope so this ordering holds.
"""

import os

# --- must run before app imports ---------------------------------------------
TEST_DB_NAME = "plutus_test"
TEST_DB_URL = f"postgresql://plutus:plutus@localhost:5433/{TEST_DB_NAME}"
ADMIN_DB_URL = "postgresql+psycopg://plutus:plutus@localhost:5433/plutus"
TEST_REDIS_URL = "redis://localhost:6379/1"
TEST_TOKEN = "integration-test-token"

os.environ["DATABASE_URL"] = TEST_DB_URL
os.environ["REDIS_URL"] = TEST_REDIS_URL
os.environ["APP_AUTH_TOKEN"] = TEST_TOKEN
os.environ["TIINGO_API_KEY"] = "test-tiingo-key"
os.environ["TWELVEDATA_API_KEY"] = "test-twelvedata-key"
os.environ["COINGECKO_API_KEY"] = ""
os.environ["PROVIDER_STOCKS"] = "tiingo"
os.environ["PROVIDER_CRYPTO"] = "binance"
os.environ["PROVIDER_FOREX"] = "twelvedata"
# alert channels MUST be blank: the developer's real .env has live SMTP creds, and
# without this a "no channels configured" test once sent an actual email mid-suite
os.environ["SMTP_HOST"] = ""
os.environ["SMTP_USER"] = ""
os.environ["SMTP_PASS"] = ""
os.environ["ALERT_EMAIL_TO"] = ""
os.environ["ALERT_EMAIL_FROM"] = ""
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TELEGRAM_CHAT_ID"] = ""
# agent layer: never probe a real sidecar or spend real tokens from tests
os.environ["CLAUDE_SIDECAR_URL"] = "http://127.0.0.1:1"
os.environ["LLM_PROVIDER"] = "claude-subscription"
os.environ["FERNET_KEY"] = ""
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["OPENAI_API_KEY"] = ""
os.environ["GOOGLE_API_KEY"] = ""
os.environ["OPENROUTER_API_KEY"] = ""
# ------------------------------------------------------------------------------

import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.core.config import get_settings

get_settings.cache_clear()

FIXTURES = Path(__file__).parent.parent / "fixtures"

pytestmark = pytest.mark.integration


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(scope="session", autouse=True)
def test_database():
    try:
        admin = sa.create_engine(ADMIN_DB_URL, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(sa.text(f"DROP DATABASE IF EXISTS {TEST_DB_NAME} WITH (FORCE)"))
            conn.execute(sa.text(f"CREATE DATABASE {TEST_DB_NAME}"))
        admin.dispose()
    except sa.exc.OperationalError:
        pytest.skip("compose db not reachable on localhost:5433 — run `make infra` first")

    # Running the real migration here validates the hypertable + compression DDL
    from alembic.config import Config

    from alembic import command

    command.upgrade(Config("alembic.ini"), "head")
    yield

    from app.core.db import dispose_engine

    dispose_engine()


@pytest.fixture(autouse=True)
def clean_state(test_database):
    yield
    from app.core.db import session_scope
    from app.providers.registry import _shared_redis, reset_registry

    with session_scope() as session:
        session.execute(
            sa.text(
                "TRUNCATE ohlcv, ingestion_runs, assets, asset_metrics, fundamentals, "
                "watchlist_items, asset_notes, news_items, screens, backtests, "
                "mandates, scans, candidates, notifications, "
                "accounts, transactions, bank_investments, "
                "app_settings, agent_conversations, agent_messages, agent_tool_calls, "
                "strategy_translations, alert_rules, exchange_links, exchange_sync_runs "
                "RESTART IDENTITY CASCADE"
            )
        )
        # watchlists: keep the migration-seeded Default row, drop the rest
        session.execute(sa.text("DELETE FROM watchlists WHERE name <> 'Default'"))
        session.execute(sa.text("DELETE FROM watchlist_items"))
    _shared_redis().flushdb()
    reset_registry()


def mock_all_providers(respx_mock, *, tiingo_response=None):
    """Route the three providers' HTTP to golden fixtures (or an override for tiingo)."""
    import httpx

    respx_mock.get(url__regex=r"https://api\.tiingo\.com/tiingo/daily/.+/prices.*").mock(
        return_value=tiingo_response
        or httpx.Response(200, json=load_fixture("tiingo_daily.json"))
    )
    respx_mock.get(url__regex=r"https://data-api\.binance\.vision/api/v3/klines.*").mock(
        return_value=httpx.Response(200, json=load_fixture("binance_klines.json"))
    )
    respx_mock.get(url__regex=r"https://api\.twelvedata\.com/time_series.*").mock(
        return_value=httpx.Response(200, json=load_fixture("twelvedata_time_series.json"))
    )
