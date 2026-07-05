"""Phase 6 integration: the chat HTTP surface — conversation CRUD, the SSE
message stream (parsed frame-by-frame), confirmation endpoints, and the
sidecar tool-execute callback."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db import session_scope
from app.ingestion.seed import seed_assets
from app.llm.providers import set_provider_override
from app.llm.providers.fake import FakeProvider
from app.llm.types import LLMResponse, ToolCallRequest, Usage
from app.models import AgentToolCall, AssetNote, Mandate
from tests.integration.conftest import TEST_TOKEN

pytestmark = pytest.mark.integration

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture(autouse=True)
def clear_override():
    yield
    set_provider_override(None)


@pytest.fixture
def client():
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def parse_sse(text: str) -> list[tuple[str, dict]]:
    import json

    events = []
    event_name = "message"
    for line in text.splitlines():
        if line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            events.append((event_name, json.loads(line[5:].strip())))
            event_name = "message"
    return events


def new_conversation(client) -> int:
    response = client.post("/api/v1/agent/conversations", json={}, headers=AUTH)
    assert response.status_code == 201, response.text
    return response.json()["id"]


class TestConversationCrud:
    def test_create_list_patch_delete(self, client):
        conversation_id = new_conversation(client)
        listed = client.get("/api/v1/agent/conversations", headers=AUTH).json()
        assert [c["id"] for c in listed] == [conversation_id]

        patched = client.patch(
            f"/api/v1/agent/conversations/{conversation_id}",
            json={"autonomous": True, "title": "research"},
            headers=AUTH,
        ).json()
        assert patched["autonomous"] is True and patched["title"] == "research"

        assert client.delete(
            f"/api/v1/agent/conversations/{conversation_id}", headers=AUTH
        ).status_code == 204
        assert client.get(
            f"/api/v1/agent/conversations/{conversation_id}", headers=AUTH
        ).status_code == 404


class TestMessageStream:
    def test_sse_turn_with_tool(self, client):
        seed_assets()
        set_provider_override(FakeProvider(script=[
            LLMResponse(text=None, tool_calls=[
                ToolCallRequest(id="t1", name="search_assets",
                                arguments={"query": "AAPL"})
            ], usage=Usage(90, 20), stop_reason="tool_use"),
            LLMResponse(text="Apple is tracked.", usage=Usage(120, 18)),
        ]))
        conversation_id = new_conversation(client)
        response = client.post(
            f"/api/v1/agent/conversations/{conversation_id}/messages",
            json={"content": "is apple tracked?"},
            headers=AUTH,
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = parse_sse(response.text)
        assert [name for name, _ in events] == [
            "start", "tool_call", "tool_result", "text_delta", "done",
        ]
        done = events[-1][1]
        assert done["input_tokens"] == 210 and done["output_tokens"] == 38

        # transcript rebuild carries the same story
        detail = client.get(
            f"/api/v1/agent/conversations/{conversation_id}", headers=AUTH
        ).json()
        roles = [m["role"] for m in detail["messages"]]
        assert roles == ["user", "assistant", "tool", "assistant"]

    def test_confirmation_roundtrip_over_http(self, client):
        seed_assets()
        set_provider_override(FakeProvider(script=[
            LLMResponse(text=None, tool_calls=[
                ToolCallRequest(id="t1", name="write_research_note",
                                arguments={"symbol": "AAPL", "markdown": "memo"})
            ], usage=Usage(60, 10), stop_reason="tool_use"),
            LLMResponse(text="Proposed a note.", usage=Usage(70, 9)),
        ]))
        conversation_id = new_conversation(client)
        response = client.post(
            f"/api/v1/agent/conversations/{conversation_id}/messages",
            json={"content": "note apple down"},
            headers=AUTH,
        )
        events = dict(parse_sse(response.text))
        confirmation_id = events["confirmation_required"]["confirmation_id"]

        detail = client.get(
            f"/api/v1/agent/conversations/{conversation_id}", headers=AUTH
        ).json()
        assert [c["id"] for c in detail["pending_confirmations"]] == [confirmation_id]

        approved = client.post(
            f"/api/v1/agent/confirmations/{confirmation_id}/approve", headers=AUTH
        ).json()
        assert approved["ok"] is True
        with session_scope() as session:
            assert session.scalar(select(AssetNote)).source == "ai"

        # second approve conflicts
        assert client.post(
            f"/api/v1/agent/confirmations/{confirmation_id}/approve", headers=AUTH
        ).status_code == 409

    def test_reject_over_http(self, client):
        seed_assets()
        set_provider_override(FakeProvider(script=[
            LLMResponse(text=None, tool_calls=[
                ToolCallRequest(id="t1", name="write_research_note",
                                arguments={"symbol": "AAPL", "markdown": "memo"})
            ], usage=Usage(60, 10), stop_reason="tool_use"),
            LLMResponse(text="Proposed.", usage=Usage(70, 9)),
        ]))
        conversation_id = new_conversation(client)
        response = client.post(
            f"/api/v1/agent/conversations/{conversation_id}/messages",
            json={"content": "note it"}, headers=AUTH,
        )
        confirmation_id = dict(parse_sse(response.text))["confirmation_required"][
            "confirmation_id"
        ]
        rejected = client.post(
            f"/api/v1/agent/confirmations/{confirmation_id}/reject", headers=AUTH
        ).json()
        assert rejected["status"] == "rejected"
        with session_scope() as session:
            assert session.scalar(select(AssetNote)) is None


class TestToolExecuteCallback:
    def test_callback_runs_read_tool(self, client):
        seed_assets()
        response = client.post(
            "/api/v1/agent/tools/execute",
            json={"name": "search_assets", "arguments": {"query": "AAPL"}},
            headers=AUTH,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["_meta"]["ok"] is True

    def test_callback_gates_writes_without_autonomous(self, client):
        seed_assets()
        conversation_id = new_conversation(client)
        response = client.post(
            "/api/v1/agent/tools/execute",
            json={
                "name": "create_mandate",
                "arguments": {"spec": {
                    "name": "Via sidecar", "asset_class": "stock",
                    "universe_def": {"type": "class"}, "schedule": "0 8 * * *",
                    "score_weights": {"rsi_extreme": 1.0},
                }},
                "conversation_id": conversation_id,
            },
            headers=AUTH,
        )
        body = response.json()
        assert body["status"] == "needs_confirmation"
        with session_scope() as session:
            assert session.scalar(select(Mandate)) is None
            row = session.get(AgentToolCall, body["_meta"]["confirmation_id"])
            assert row.status == "pending_confirmation"

    def test_callback_requires_auth(self, client):
        response = client.post(
            "/api/v1/agent/tools/execute",
            json={"name": "search_assets", "arguments": {"query": "a"}},
        )
        assert response.status_code == 401
