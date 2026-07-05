"""Phase 6 integration: LLM settings storage (encryption, masking, env override),
token usage endpoint, and the agent actions feed."""

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import session_scope
from tests.integration.conftest import TEST_TOKEN

pytestmark = pytest.mark.integration

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
    # keep the sidecar probe from hanging on a real socket
    monkeypatch.setenv("CLAUDE_SIDECAR_URL", "http://127.0.0.1:1")
    get_settings.cache_clear()
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


class TestSettingsStorage:
    def test_get_defaults_from_env(self, client):
        body = client.get("/api/v1/agent/settings", headers=AUTH).json()
        assert body["provider"] == "claude-subscription"
        assert body["keys"]["anthropic_api_key"] is None
        assert body["fernet_configured"] is True
        assert body["sidecar"]["reachable"] is False

    def test_put_key_stored_encrypted_and_masked(self, client):
        raw_key = "sk-ant-api03-super-secret-value-9x8y"
        response = client.put(
            "/api/v1/agent/settings",
            json={"provider": "anthropic-api", "keys": {"anthropic_api_key": raw_key}},
            headers=AUTH,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["provider"] == "anthropic-api"
        masked = body["keys"]["anthropic_api_key"]
        assert masked is not None and raw_key not in masked and len(masked) < 15

        # the raw key must not exist anywhere in the row
        with session_scope() as session:
            stored = session.execute(
                text("SELECT value, is_secret FROM app_settings WHERE key='anthropic_api_key'")
            ).one()
        assert stored.is_secret is True
        assert raw_key not in stored.value

        # but the resolver decrypts it for provider construction
        from app.llm.settings_store import get_llm_settings

        with session_scope() as session:
            assert get_llm_settings(session).anthropic_api_key == raw_key

    def test_db_overrides_env_without_restart(self, client):
        client.put("/api/v1/agent/settings", json={"provider": "ollama"}, headers=AUTH)
        body = client.get("/api/v1/agent/settings", headers=AUTH).json()
        assert body["provider"] == "ollama"

    def test_unknown_key_name_422(self, client):
        response = client.put(
            "/api/v1/agent/settings",
            json={"keys": {"llm_provider": "sneaky"}},
            headers=AUTH,
        )
        assert response.status_code == 422

    def test_secret_without_fernet_key_422(self, client, monkeypatch):
        monkeypatch.setenv("FERNET_KEY", "")
        get_settings.cache_clear()
        response = client.put(
            "/api/v1/agent/settings",
            json={"keys": {"openai_api_key": "sk-abc"}},
            headers=AUTH,
        )
        assert response.status_code == 422
        assert "FERNET_KEY" in response.text


class TestUsageAndActions:
    def test_usage_empty(self, client):
        body = client.get("/api/v1/agent/usage", headers=AUTH).json()
        assert body["tokens_used"] == 0
        assert body["remaining"] == body["daily_token_budget"]

    def test_usage_sums_todays_messages(self, client):
        from app.models import AgentConversation, AgentMessage

        with session_scope() as session:
            conversation = AgentConversation(kind="chat")
            session.add(conversation)
            session.flush()
            session.add_all(
                [
                    AgentMessage(
                        conversation_id=conversation.id,
                        role="assistant",
                        content="hi",
                        input_tokens=1200,
                        output_tokens=300,
                    ),
                    AgentMessage(
                        conversation_id=conversation.id,
                        role="assistant",
                        content="again",
                        input_tokens=100,
                        output_tokens=50,
                    ),
                ]
            )
        body = client.get("/api/v1/agent/usage", headers=AUTH).json()
        assert body["tokens_used"] == 1650

    def test_actions_feed_lists_and_filters(self, client):
        from app.models import AgentToolCall

        with session_scope() as session:
            session.add_all(
                [
                    AgentToolCall(
                        source="mcp", tier="write", name="create_mandate",
                        arguments={"name": "x"}, status="done", result_summary="created",
                    ),
                    AgentToolCall(
                        source="app", tier="read", name="get_news",
                        arguments={"symbol": "AAPL"}, status="done",
                    ),
                ]
            )
        all_actions = client.get("/api/v1/agent/actions", headers=AUTH).json()
        assert len(all_actions) == 2
        mcp_only = client.get("/api/v1/agent/actions?source=mcp", headers=AUTH).json()
        assert [a["name"] for a in mcp_only] == ["create_mandate"]
        write_only = client.get("/api/v1/agent/actions?tier=write", headers=AUTH).json()
        assert [a["source"] for a in write_only] == ["mcp"]
