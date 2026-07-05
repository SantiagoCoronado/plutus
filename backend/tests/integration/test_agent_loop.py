"""Phase 6 integration: the Python agent loop end-to-end with a scripted
provider — tool rounds, confirmation gating, autonomous mode, the iteration
cap, and budget cutoffs."""

import asyncio

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import session_scope
from app.ingestion.seed import seed_assets
from app.llm.loop import run_agent_turn
from app.llm.providers import set_provider_override
from app.llm.providers.fake import FakeProvider
from app.llm.types import LLMResponse, ToolCallRequest, Usage
from app.models import AgentConversation, AgentMessage, AgentToolCall, AssetNote

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def clear_override():
    yield
    set_provider_override(None)


@pytest.fixture
def conversation():
    seed_assets()
    with session_scope() as session:
        row = AgentConversation(kind="chat")
        session.add(row)
        session.flush()
        return row.id


def collect_events(conversation_id: int, text: str):
    async def _run():
        return [event async for event in run_agent_turn(conversation_id, text)]

    return asyncio.run(_run())


def tool_response(name: str, arguments: dict, call_id: str = "tc_1") -> LLMResponse:
    return LLMResponse(
        text=None,
        tool_calls=[ToolCallRequest(id=call_id, name=name, arguments=arguments)],
        usage=Usage(100, 20),
        stop_reason="tool_use",
    )


class TestPlainTurn:
    def test_text_only_turn(self, conversation):
        set_provider_override(FakeProvider(script=[
            LLMResponse(text="AAPL looks steady.", usage=Usage(50, 10)),
        ]))
        events = collect_events(conversation, "how is AAPL?")
        kinds = [event.type for event in events]
        assert kinds == ["start", "text_delta", "done"]
        assert events[1].data["text"] == "AAPL looks steady."
        assert events[2].data["input_tokens"] == 50

        with session_scope() as session:
            rows = session.scalars(
                select(AgentMessage).order_by(AgentMessage.id)
            ).all()
            assert [r.role for r in rows] == ["user", "assistant"]
            assert rows[1].output_tokens == 10
            convo = session.get(AgentConversation, conversation)
            assert convo.title == "how is AAPL?"
            assert convo.provider == "fake"

    def test_tool_round_then_answer(self, conversation):
        fake = FakeProvider(script=[
            tool_response("search_assets", {"query": "AAPL"}),
            LLMResponse(text="Found it.", usage=Usage(80, 15)),
        ])
        set_provider_override(fake)
        events = collect_events(conversation, "find apple")
        kinds = [event.type for event in events]
        assert kinds == ["start", "tool_call", "tool_result", "text_delta", "done"]
        assert events[2].data["ok"] is True

        with session_scope() as session:
            roles = [r.role for r in session.scalars(
                select(AgentMessage).order_by(AgentMessage.id)).all()]
            assert roles == ["user", "assistant", "tool", "assistant"]
            audit = session.scalars(select(AgentToolCall)).all()
            assert len(audit) == 1 and audit[0].source == "app"
        # the second provider call saw the tool result message
        assert any(m.role == "tool" for m in fake.calls[1]["messages"])


class TestConfirmationGating:
    def test_write_tool_needs_confirmation_by_default(self, conversation):
        set_provider_override(FakeProvider(script=[
            tool_response("write_research_note",
                          {"symbol": "AAPL", "markdown": "memo"}),
            LLMResponse(text="I proposed a note for your approval.", usage=Usage(60, 12)),
        ]))
        events = collect_events(conversation, "write a note on AAPL")
        kinds = [event.type for event in events]
        assert kinds == ["start", "tool_call", "confirmation_required",
                         "text_delta", "done"]
        confirmation = events[2].data
        assert confirmation["name"] == "write_research_note"
        with session_scope() as session:
            assert session.scalar(select(AssetNote)) is None  # nothing executed
            row = session.get(AgentToolCall, confirmation["confirmation_id"])
            assert row.status == "pending_confirmation"
            # the model saw needs_confirmation as the tool result
            tool_message = session.scalar(
                select(AgentMessage).where(AgentMessage.role == "tool")
            )
            assert tool_message.tool_result["status"] == "needs_confirmation"

    def test_autonomous_mode_executes_writes(self, conversation):
        with session_scope() as session:
            session.get(AgentConversation, conversation).autonomous = True
        set_provider_override(FakeProvider(script=[
            tool_response("write_research_note",
                          {"symbol": "AAPL", "markdown": "memo"}),
            LLMResponse(text="Note written.", usage=Usage(60, 12)),
        ]))
        events = collect_events(conversation, "write a note on AAPL")
        assert [e.type for e in events] == ["start", "tool_call", "tool_result",
                                            "text_delta", "done"]
        with session_scope() as session:
            note = session.scalar(select(AssetNote))
            assert note is not None and note.source == "ai"


class TestLimits:
    def test_iteration_cap_forces_final_answer(self, conversation, monkeypatch):
        monkeypatch.setenv("AGENT_MAX_TOOL_ITERATIONS", "2")
        get_settings.cache_clear()
        fake = FakeProvider(script=[
            tool_response("search_assets", {"query": "a"}, "t1"),
            tool_response("search_assets", {"query": "b"}, "t2"),
            LLMResponse(text="Best effort answer.", usage=Usage(40, 8)),
        ])
        set_provider_override(fake)
        events = collect_events(conversation, "loop forever")
        get_settings.cache_clear()

        assert events[-1].type == "done"
        # the final call must carry no tools and the nudge message
        final_call = fake.calls[-1]
        assert final_call["tools"] == []
        assert any(
            m.content and "exhausted" in m.content for m in final_call["messages"]
        )

    def test_budget_exceeded_blocks_turn(self, conversation, monkeypatch):
        with session_scope() as session:
            session.add(AgentMessage(
                conversation_id=conversation, role="assistant",
                content="old", input_tokens=999_999, output_tokens=1,
            ))
        monkeypatch.setenv("AGENT_DAILY_TOKEN_BUDGET", "1000")
        get_settings.cache_clear()
        set_provider_override(FakeProvider(script=[LLMResponse(text="never")]))
        events = collect_events(conversation, "hello?")
        get_settings.cache_clear()

        assert [e.type for e in events] == ["error"]
        assert "budget" in events[0].data["message"]

    def test_provider_without_key_is_clean_error(self, conversation, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "anthropic-api")
        get_settings.cache_clear()
        events = collect_events(conversation, "hi")
        get_settings.cache_clear()
        assert events[-1].type == "error"
        assert "API key" in events[-1].data["message"]

    def test_sidecar_unreachable_is_clean_error(self, conversation):
        # default test env points the sidecar at 127.0.0.1:1
        events = collect_events(conversation, "hi")
        assert events[-1].type == "error"
        assert "not reachable" in events[-1].data["message"]
