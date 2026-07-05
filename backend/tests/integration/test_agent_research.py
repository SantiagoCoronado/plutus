"""Phase 6 integration: deep-dive research tasks and the nightly memo run,
driven by a scripted provider."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import session_scope
from app.ingestion.seed import seed_assets
from app.llm.providers import set_provider_override
from app.llm.providers.fake import FakeProvider
from app.llm.research import run_deep_dive, run_nightly_memos
from app.llm.types import LLMResponse, ToolCallRequest, Usage
from app.models import (
    AgentConversation,
    AgentToolCall,
    AssetNote,
    Candidate,
    Mandate,
)
from tests.integration.conftest import TEST_TOKEN

pytestmark = pytest.mark.integration

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}

MEMO_MARKDOWN = "**Snapshot**: AAPL at 140, RSI 28.\n\n**Fundamentals**: solid."


@pytest.fixture(autouse=True)
def clear_override():
    yield
    set_provider_override(None)


def memo_script(symbol: str = "AAPL") -> list[LLMResponse]:
    return [
        LLMResponse(text=None, tool_calls=[
            ToolCallRequest(id="t1", name="get_asset_overview",
                            arguments={"symbol": symbol})
        ], usage=Usage(200, 30), stop_reason="tool_use"),
        LLMResponse(text=None, tool_calls=[
            ToolCallRequest(id="t2", name="write_research_note", arguments={
                "symbol": symbol,
                "title": f"AI research memo — {symbol}",
                "markdown": MEMO_MARKDOWN,
            })
        ], usage=Usage(300, 80), stop_reason="tool_use"),
        LLMResponse(text="Memo written.", usage=Usage(100, 10)),
    ]


def make_task_conversation(asset_id: int, candidate_id: int | None = None) -> int:
    with session_scope() as session:
        conversation = AgentConversation(
            kind="task", status="queued", autonomous=True,
            task_meta={"asset_id": asset_id, "candidate_id": candidate_id,
                       "trigger": "manual"},
        )
        session.add(conversation)
        session.flush()
        return conversation.id


def seed_candidate(asset_id: int, score: float = 80.0) -> int:
    with session_scope() as session:
        mandate = session.scalar(select(Mandate))
        if mandate is None:
            mandate = Mandate(
                name="Oversold", asset_class="stock",
                universe_def={"type": "class"}, schedule="30 7 * * 1-5",
                score_weights={"rsi_extreme": 2.0}, min_score=40.0,
            )
            session.add(mandate)
            session.flush()
        candidate = Candidate(
            mandate_id=mandate.id, asset_id=asset_id, ts=datetime.now(UTC),
            score=score, signals=[],
        )
        session.add(candidate)
        session.flush()
        return candidate.id


class TestDeepDive:
    def test_deep_dive_writes_labeled_memo(self, monkeypatch):
        assets = {symbol: asset_id for asset_id, symbol in seed_assets()}
        candidate_id = seed_candidate(assets["AAPL"])
        conversation_id = make_task_conversation(assets["AAPL"], candidate_id)
        set_provider_override(FakeProvider(script=memo_script()))

        note_id = run_deep_dive(conversation_id)
        assert note_id > 0

        with session_scope() as session:
            note = session.get(AssetNote, note_id)
            assert note.source == "ai"
            assert "AI-generated, informational only" in note.body_md
            conversation = session.get(AgentConversation, conversation_id)
            assert conversation.status == "done"
            candidate = session.get(Candidate, candidate_id)
            assert candidate.context["memo_note_id"] == note_id
            audit_sources = {
                row.source for row in session.scalars(select(AgentToolCall)).all()
            }
            assert audit_sources == {"task"}

    def test_deep_dive_without_memo_is_error_state(self):
        assets = {symbol: asset_id for asset_id, symbol in seed_assets()}
        conversation_id = make_task_conversation(assets["AAPL"])
        set_provider_override(FakeProvider(script=[
            LLMResponse(text="I looked but wrote nothing.", usage=Usage(50, 10)),
        ]))
        assert run_deep_dive(conversation_id) == 0
        with session_scope() as session:
            conversation = session.get(AgentConversation, conversation_id)
            assert conversation.status == "error"
            assert "without writing a memo" in conversation.error

    def test_budget_exceeded_skips_cleanly(self, monkeypatch):
        assets = {symbol: asset_id for asset_id, symbol in seed_assets()}
        conversation_id = make_task_conversation(assets["AAPL"])
        with session_scope() as session:
            from app.models import AgentMessage

            session.add(AgentMessage(
                conversation_id=conversation_id, role="assistant",
                content="x", input_tokens=999_999, output_tokens=1,
            ))
        monkeypatch.setenv("AGENT_DAILY_TOKEN_BUDGET", "1000")
        get_settings.cache_clear()
        set_provider_override(FakeProvider(script=memo_script()))
        assert run_deep_dive(conversation_id) == 0
        get_settings.cache_clear()
        with session_scope() as session:
            conversation = session.get(AgentConversation, conversation_id)
            assert conversation.status == "error"
            assert "budget" in conversation.error
            assert session.scalar(select(AssetNote)) is None


class TestNightlyMemos:
    def test_top_candidate_gets_memo_and_link(self):
        assets = {symbol: asset_id for asset_id, symbol in seed_assets()}
        candidate_id = seed_candidate(assets["AAPL"], score=90.0)
        set_provider_override(FakeProvider(script=memo_script()))

        written = run_nightly_memos()
        assert len(written) == 1

        with session_scope() as session:
            candidate = session.get(Candidate, candidate_id)
            assert candidate.context["memo_note_id"] == written[0]
            conversation = session.scalar(
                select(AgentConversation).where(AgentConversation.kind == "task")
            )
            assert conversation.status == "done"
            assert conversation.task_meta["trigger"] == "nightly"

    def test_candidates_below_threshold_skipped(self):
        assets = {symbol: asset_id for asset_id, symbol in seed_assets()}
        seed_candidate(assets["AAPL"], score=10.0)  # below min_score 40
        set_provider_override(FakeProvider(script=memo_script()))
        assert run_nightly_memos() == []

    def test_already_memoed_candidates_skipped(self):
        assets = {symbol: asset_id for asset_id, symbol in seed_assets()}
        candidate_id = seed_candidate(assets["AAPL"], score=90.0)
        with session_scope() as session:
            candidate = session.get(Candidate, candidate_id)
            candidate.context = {"memo_note_id": 123}
        set_provider_override(FakeProvider(script=memo_script()))
        assert run_nightly_memos() == []


class TestDeepDiveEndpoint:
    @pytest.fixture
    def client(self):
        from app.main import create_app

        with TestClient(create_app()) as test_client:
            yield test_client

    def test_enqueue_by_asset(self, client, monkeypatch):
        assets = {symbol: asset_id for asset_id, symbol in seed_assets()}
        calls = []
        import worker.tasks as tasks

        monkeypatch.setattr(tasks.run_agent_deep_dive, "delay",
                            lambda cid: calls.append(cid))
        response = client.post(
            "/api/v1/agent/deep-dives",
            json={"asset_id": assets["AAPL"]}, headers=AUTH,
        )
        assert response.status_code == 202
        body = response.json()
        assert body["symbol"] == "AAPL"
        assert calls == [body["conversation_id"]]
        with session_scope() as session:
            conversation = session.get(AgentConversation, body["conversation_id"])
            assert conversation.kind == "task" and conversation.status == "queued"

    def test_enqueue_by_candidate(self, client, monkeypatch):
        assets = {symbol: asset_id for asset_id, symbol in seed_assets()}
        candidate_id = seed_candidate(assets["AAPL"])
        import worker.tasks as tasks

        monkeypatch.setattr(tasks.run_agent_deep_dive, "delay", lambda cid: None)
        response = client.post(
            "/api/v1/agent/deep-dives",
            json={"candidate_id": candidate_id}, headers=AUTH,
        )
        assert response.status_code == 202
        with session_scope() as session:
            conversation = session.scalar(
                select(AgentConversation).where(AgentConversation.kind == "task")
            )
            assert conversation.task_meta["candidate_id"] == candidate_id

    def test_missing_target_422(self, client):
        assert client.post("/api/v1/agent/deep-dives", json={},
                           headers=AUTH).status_code == 422
