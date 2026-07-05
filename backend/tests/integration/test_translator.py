"""Phase 6 integration (spec §13.5): content → draft + fidelity report →
confirm gate → queued backtest; server-side re-validation and retry."""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db import session_scope
from app.ingestion.seed import seed_assets
from app.llm.providers import set_provider_override
from app.llm.providers.fake import FakeProvider
from app.llm.types import LLMResponse, Usage
from app.models import Backtest, StrategyTranslation
from tests.integration.conftest import TEST_TOKEN

pytestmark = pytest.mark.integration

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}

ARTICLE = (
    "I read about a simple dip-buying idea for Apple: buy whenever the 14-day RSI "
    "drops under 30, sell when it gets back over 55, and always cut losses at 8%. "
    "The author also day-trades it on the 4-hour chart with options spreads."
)

GOOD_JSON = {
    "translatable": True,
    "symbol": "AAPL",
    "understanding_md": "Buy when RSI(14) < 30; sell when RSI(14) > 55; 8% stop-loss.",
    "limitations": [
        "source also trades a 4-hour chart; this backtest is daily bars only",
        "options spreads cannot be expressed and are ignored",
    ],
    "spec": {
        "entry": {"field": "rsi_14", "op": "<", "value": 30},
        "exit": {"field": "rsi_14", "op": ">", "value": 55},
        "stop_loss_pct": 0.08,
    },
}


@pytest.fixture(autouse=True)
def clear_override():
    yield
    set_provider_override(None)


@pytest.fixture
def client():
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def fake(*texts: str) -> FakeProvider:
    return FakeProvider(script=[
        LLMResponse(text=text, usage=Usage(500, 200)) for text in texts
    ])


class TestTranslationPipeline:
    def test_happy_path_produces_draft_with_fidelity_report(self, client):
        seed_assets()
        set_provider_override(fake(json.dumps(GOOD_JSON)))
        response = client.post("/api/v1/translations",
                               json={"content": ARTICLE}, headers=AUTH)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["status"] == "draft"
        assert body["translatable"] is True
        assert body["symbol"] == "AAPL" and body["asset_id"] is not None
        assert "RSI(14) < 30" in body["understanding_md"]
        assert len(body["limitations"]) == 2
        assert body["spec"]["entry"] == {"field": "rsi_14", "op": "<", "value": 30}

    def test_invalid_ast_retries_then_fails(self, client):
        seed_assets()
        bad = json.dumps({**GOOD_JSON, "spec": {
            "entry": {"field": "made_up_indicator", "op": "<", "value": 30},
            "exit": GOOD_JSON["spec"]["exit"],
        }})
        set_provider_override(fake(bad, bad))
        response = client.post("/api/v1/translations",
                               json={"content": ARTICLE}, headers=AUTH)
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "failed"
        assert "made_up_indicator" in body["error"]

    def test_retry_can_recover(self, client):
        seed_assets()
        set_provider_override(fake("here you go: not json at all",
                                   json.dumps(GOOD_JSON)))
        body = client.post("/api/v1/translations",
                           json={"content": ARTICLE}, headers=AUTH).json()
        assert body["status"] == "draft"

    def test_untranslatable_content(self, client):
        seed_assets()
        set_provider_override(fake(json.dumps({
            "translatable": False, "symbol": None, "understanding_md": None,
            "limitations": ["the strategy is an options iron condor — no equity legs"],
            "spec": None,
        })))
        body = client.post("/api/v1/translations",
                           json={"content": ARTICLE}, headers=AUTH).json()
        assert body["status"] == "draft" and body["translatable"] is False
        confirm = client.post(f"/api/v1/translations/{body['id']}/confirm", headers=AUTH)
        assert confirm.status_code == 422

    def test_untracked_symbol_appends_limitation_and_blocks_confirm(self, client):
        seed_assets()
        set_provider_override(fake(json.dumps({**GOOD_JSON, "symbol": "ZZZQ"})))
        body = client.post("/api/v1/translations",
                           json={"content": ARTICLE}, headers=AUTH).json()
        assert body["asset_id"] is None
        assert any("not tracked" in item for item in body["limitations"])
        confirm = client.post(f"/api/v1/translations/{body['id']}/confirm", headers=AUTH)
        assert confirm.status_code == 422

    def test_candlestick_condition_is_legal_vocabulary(self, client):
        seed_assets()
        payload = {**GOOD_JSON, "spec": {
            "entry": {"all": [
                {"field": "hammer", "op": "==", "value": 1},
                {"field": "close", "op": "<", "value": {"field": "sma_50"}},
            ]},
            "exit": {"field": "close", "op": "crosses_above", "value": {"field": "sma_20"}},
        }}
        set_provider_override(fake(json.dumps(payload)))
        body = client.post("/api/v1/translations",
                           json={"content": ARTICLE}, headers=AUTH).json()
        assert body["status"] == "draft", body


class TestConfirmGate:
    def _draft(self, client) -> dict:
        seed_assets()
        set_provider_override(fake(json.dumps(GOOD_JSON)))
        return client.post("/api/v1/translations",
                           json={"content": ARTICLE}, headers=AUTH).json()

    def test_confirm_enqueues_backtest_once(self, client, monkeypatch):
        draft = self._draft(client)
        calls = []
        import worker.tasks as tasks

        monkeypatch.setattr(tasks.run_backtest, "delay", lambda bt: calls.append(bt))
        confirmed = client.post(f"/api/v1/translations/{draft['id']}/confirm",
                                headers=AUTH)
        assert confirmed.status_code == 201, confirmed.text
        backtest_id = confirmed.json()["backtest_id"]
        assert calls == [backtest_id]
        with session_scope() as session:
            backtest = session.get(Backtest, backtest_id)
            assert backtest.kind == "strategy"
            assert backtest.params["symbol"] == "AAPL"
            assert backtest.params["stop_loss_pct"] == 0.08
            translation = session.get(StrategyTranslation, draft["id"])
            assert translation.status == "confirmed"
            assert translation.backtest_id == backtest_id

        # a confirmed draft can't run twice
        again = client.post(f"/api/v1/translations/{draft['id']}/confirm", headers=AUTH)
        assert again.status_code == 409

    def test_discard_blocks_confirm(self, client):
        draft = self._draft(client)
        assert client.post(f"/api/v1/translations/{draft['id']}/discard",
                           headers=AUTH).status_code == 200
        assert client.post(f"/api/v1/translations/{draft['id']}/confirm",
                           headers=AUTH).status_code == 409

    def test_provenance_persisted(self, client):
        draft = self._draft(client)
        with session_scope() as session:
            translation = session.get(StrategyTranslation, draft["id"])
            assert ARTICLE[:50] in translation.source_content
            assert translation.provider == "fake"
            # the raw model output is kept on the translate conversation
            from app.models import AgentMessage

            raw = session.scalars(select(AgentMessage).where(
                AgentMessage.conversation_id == translation.conversation_id
            )).all()
            assert len(raw) == 1 and raw[0].output_tokens == 200


class TestTranslateStrategyTool:
    def test_tool_returns_draft_and_runs_nothing(self, client):
        seed_assets()
        set_provider_override(fake(json.dumps(GOOD_JSON)))
        from app.core.db import SessionLocal
        from app.llm.executor import execute_tool

        session = SessionLocal()
        try:
            outcome = execute_tool(
                session, "translate_strategy",
                {"content": ARTICLE}, source="app",
            )
        finally:
            session.close()
        assert outcome.ok, outcome.error
        assert outcome.result["translation_id"] > 0
        assert "confirm" in outcome.result["note"]
        with session_scope() as check:
            assert check.scalar(select(Backtest)) is None
