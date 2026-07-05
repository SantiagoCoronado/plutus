"""Phase 6 integration: every agent tool handler against a seeded db, plus the
executor chokepoint (tier gate, schema gate, confirmation flow, audit rows)."""

from datetime import UTC, date, datetime

import numpy as np
import pytest
from sqlalchemy import select

from app.analysis.metrics import _upsert_metrics
from app.core.db import SessionLocal, session_scope
from app.ingestion.eod import upsert_candles
from app.ingestion.seed import seed_assets
from app.llm.executor import (
    ConfirmationError,
    approve_confirmation,
    execute_tool,
    reject_confirmation,
)
from app.models import (
    AgentToolCall,
    AssetNote,
    Candidate,
    Fundamentals,
    Mandate,
    NewsItem,
    Scan,
    Transaction,
    WatchlistItem,
)

pytestmark = pytest.mark.integration

N_BARS = 400


@pytest.fixture
def seeded():
    """Tracked seed assets + AAPL bars/metrics/fundamentals/news + a mandate + account."""
    assets = {symbol: asset_id for asset_id, symbol in seed_assets()}
    aapl = assets["AAPL"]
    with session_scope() as session:
        closes = np.linspace(100, 140, N_BARS)
        end = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        rows = [
            {
                "asset_id": aapl,
                "interval": "1d",
                "ts": end - __import__("datetime").timedelta(days=N_BARS - i),
                "open": closes[i] - 0.5,
                "high": closes[i] + 2.0,
                "low": closes[i] - 2.5,
                "close": closes[i],
                "volume": 1e6,
            }
            for i in range(N_BARS)
        ]
        upsert_candles(session, rows)
        _upsert_metrics(
            session,
            aapl,
            {"as_of": datetime.now(UTC).date(), "close": 140.0, "rsi_14": 28.0, "pe": 25.0},
        )
        session.add(
            Fundamentals(
                asset_id=aapl, period="annual", report_date=date(2025, 9, 30),
                fiscal_year=2025, provider="fmp", revenue=4e11, eps=7.1, roe=1.5,
            )
        )
        session.add(
            NewsItem(
                ts=datetime.now(UTC), source="reuters",
                headline="Apple ships something shiny", url="https://x.test/1",
                tickers=["AAPL"], sentiment=0.4,
            )
        )
        mandate = Mandate(
            name="Oversold large caps", asset_class="stock",
            universe_def={"type": "class"}, rules=None, schedule="30 7 * * 1-5",
            score_weights={"rsi_extreme": 2.0},
        )
        session.add(mandate)
        session.flush()
        session.add(
            Candidate(
                mandate_id=mandate.id, asset_id=aapl, ts=datetime.now(UTC),
                score=71.0, signals=[{"key": "rsi_extreme", "score": 71.0, "triggered": True}],
            )
        )
        from app.models import Account

        session.add(Account(name="Bitso", type="exchange", currency="MXN"))
        session.flush()
        mandate_id = mandate.id
    return {"assets": assets, "mandate_id": mandate_id}


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


def run(db, name, args, **kwargs):
    kwargs.setdefault("source", "app")
    return execute_tool(db, name, args, **kwargs)


class TestReadTools:
    def test_search_assets(self, seeded, db):
        outcome = run(db, "search_assets", {"query": "app"})
        assert outcome.ok
        assert any(r["symbol"] == "AAPL" for r in outcome.result["results"])

    def test_get_asset_overview(self, seeded, db):
        outcome = run(db, "get_asset_overview", {"symbol": "aapl"})
        assert outcome.ok
        assert outcome.result["metrics"]["rsi_14"] == 28.0

    def test_get_ohlcv_capped_rows(self, seeded, db):
        outcome = run(db, "get_ohlcv", {"symbol": "AAPL", "lookback_days": 1825})
        assert outcome.ok
        assert 0 < len(outcome.result["candles"]) <= 500
        first = outcome.result["candles"][0]
        assert len(first) == 6 and isinstance(first[0], str)

    def test_get_fundamentals(self, seeded, db):
        outcome = run(db, "get_fundamentals", {"symbol": "AAPL"})
        assert outcome.ok
        assert outcome.result["annual"][0]["eps"] == 7.1

    def test_get_news(self, seeded, db):
        outcome = run(db, "get_news", {"symbol": "AAPL", "days": 3})
        assert outcome.ok
        assert outcome.result["headlines"][0]["source"] == "reuters"

    def test_run_screen(self, seeded, db):
        outcome = run(
            db, "run_screen",
            {"filter_ast": {"field": "rsi_14", "op": "<", "value": 30},
             "asset_class": "stock"},
        )
        assert outcome.ok
        assert [m["symbol"] for m in outcome.result["matches"]] == ["AAPL"]

    def test_backtest_signal(self, seeded, db):
        outcome = run(
            db, "backtest_signal",
            {"symbol": "AAPL",
             "entry_condition": {"field": "close", "op": ">", "value": 120}},
        )
        assert outcome.ok
        assert outcome.result["past_triggers"] >= 1
        assert "20d" in outcome.result["forward_returns_after_trigger"]

    def test_get_candidates_and_mandates(self, seeded, db):
        candidates = run(db, "get_candidates", {"status": "new"})
        assert candidates.ok and candidates.result["candidates"][0]["symbol"] == "AAPL"
        mandates = run(db, "get_mandates", {})
        assert mandates.ok and mandates.result["mandates"][0]["name"] == "Oversold large caps"

    def test_portfolio_tools(self, seeded, db):
        positions = run(db, "get_portfolio_positions", {})
        assert positions.ok and positions.result["currency"] == "USD"
        performance = run(db, "get_portfolio_performance", {"period": "1m"})
        assert performance.ok

    def test_ingestion_status(self, seeded, db):
        outcome = run(db, "get_ingestion_status", {})
        assert outcome.ok

    def test_unknown_symbol_is_error_result(self, seeded, db):
        outcome = run(db, "get_asset_overview", {"symbol": "ZZZQ"})
        assert not outcome.ok
        assert "not a tracked asset" in outcome.error


class TestWriteTools:
    def test_write_research_note_footer_and_label(self, seeded, db):
        outcome = run(db, "write_research_note",
                      {"symbol": "AAPL", "title": "memo", "markdown": "Solid quarter."})
        assert outcome.ok
        note = db.get(AssetNote, outcome.result["note_id"])
        assert note.source == "ai"
        assert "AI-generated, informational only" in note.body_md

    def test_manage_watchlist_roundtrip(self, seeded, db):
        added = run(db, "manage_watchlist", {"action": "add", "symbol": "AAPL"})
        assert added.ok
        assert db.scalar(select(WatchlistItem)) is not None
        removed = run(db, "manage_watchlist", {"action": "remove", "symbol": "AAPL"})
        assert removed.ok
        db.expire_all()
        assert db.scalar(select(WatchlistItem)) is None

    def test_create_mandate_valid(self, seeded, db):
        outcome = run(db, "create_mandate", {"spec": {
            "name": "Momentum leaders",
            "asset_class": "stock",
            "universe_def": {"type": "class"},
            "schedule": "0 8 * * 1-5",
            "score_weights": {"momentum_rank": 2.0},
        }})
        assert outcome.ok, outcome.error
        assert db.get(Mandate, outcome.result["mandate_id"]).name == "Momentum leaders"

    def test_create_mandate_unknown_signal_is_error_result(self, seeded, db):
        outcome = run(db, "create_mandate", {"spec": {
            "name": "Bad", "asset_class": "stock",
            "universe_def": {"type": "class"}, "schedule": "0 8 * * *",
            "score_weights": {"astrology": 1.0},
        }})
        assert not outcome.ok
        assert "unknown signal" in outcome.error

    def test_update_mandate_patch(self, seeded, db):
        outcome = run(db, "update_mandate",
                      {"mandate_id": seeded["mandate_id"], "patch": {"active": False}})
        assert outcome.ok
        db.expire_all()
        assert db.get(Mandate, seeded["mandate_id"]).active is False

    def test_trigger_scan_enqueues(self, seeded, db, monkeypatch):
        calls = []
        import worker.tasks as tasks

        monkeypatch.setattr(tasks.run_scan, "delay", lambda scan_id: calls.append(scan_id))
        outcome = run(db, "trigger_scan", {"mandate_id": seeded["mandate_id"]})
        assert outcome.ok
        assert calls == [outcome.result["scan_id"]]
        assert db.get(Scan, outcome.result["scan_id"]).status == "queued"

    def test_update_candidate(self, seeded, db):
        candidate_id = db.scalar(select(Candidate.id))
        outcome = run(db, "update_candidate", {"candidate_id": candidate_id, "status": "starred"})
        assert outcome.ok
        db.expire_all()
        assert db.get(Candidate, candidate_id).status == "starred"

    def test_run_strategy_backtest_enqueues(self, seeded, db, monkeypatch):
        calls = []
        import worker.tasks as tasks

        monkeypatch.setattr(tasks.run_backtest, "delay", lambda bt_id: calls.append(bt_id))
        outcome = run(db, "run_strategy_backtest", {"spec": {
            "symbol": "AAPL",
            "entry": {"field": "close", "op": ">", "value": 120},
            "exit": {"field": "close", "op": "<", "value": 110},
        }})
        assert outcome.ok, outcome.error
        assert calls == [outcome.result["backtest_id"]]

    def test_add_transaction_and_oversell_error(self, seeded, db):
        buy = run(db, "add_transaction", {"record": {
            "account": "Bitso", "symbol": "BTC", "type": "buy",
            "ts": "2026-01-05T12:00:00Z", "quantity": 0.5,
            "price": 1_000_000, "currency": "MXN",
        }})
        assert buy.ok, buy.error
        assert db.get(Transaction, buy.result["transaction_id"]).type == "buy"

        oversell = run(db, "add_transaction", {"record": {
            "account": "Bitso", "symbol": "BTC", "type": "sell",
            "ts": "2026-02-05T12:00:00Z", "quantity": 2.0,
            "price": 1_100_000, "currency": "MXN",
        }})
        assert not oversell.ok
        assert "invalid transaction" in oversell.error


class TestExecutorGates:
    def test_unknown_tool(self, seeded, db):
        outcome = run(db, "delete_transaction", {"transaction_id": 1})
        assert not outcome.ok and "unknown tool" in outcome.error

    def test_read_tier_blocks_writes(self, seeded, db):
        outcome = run(db, "write_research_note",
                      {"symbol": "AAPL", "markdown": "x"}, allowed_tier="read")
        assert not outcome.ok and "read-only" in outcome.error
        # blocked attempts still audit
        row = db.scalars(select(AgentToolCall).order_by(AgentToolCall.id.desc())).first()
        assert row.name == "write_research_note" and row.status == "error"

    def test_schema_validation_is_error_result(self, seeded, db):
        outcome = run(db, "get_news", {"days": 7})  # missing required symbol
        assert not outcome.ok and "invalid arguments" in outcome.error

    def test_allowed_tools_scoping(self, seeded, db):
        outcome = run(db, "get_news", {"symbol": "AAPL"},
                      allowed_tools=frozenset({"get_asset_overview"}))
        assert not outcome.ok and "not available" in outcome.error

    def test_audit_row_per_call(self, seeded, db):
        before = len(db.scalars(select(AgentToolCall)).all())
        run(db, "search_assets", {"query": "aapl"})
        db.expire_all()
        after = db.scalars(select(AgentToolCall)).all()
        assert len(after) == before + 1
        assert after[-1].status == "done" and after[-1].tier == "read"


class TestConfirmationFlow:
    def test_confirm_then_approve_executes(self, seeded, db):
        proposed = run(db, "write_research_note",
                       {"symbol": "AAPL", "markdown": "Buy-quality memo."},
                       confirm_writes=True, conversation_id=None)
        assert proposed.needs_confirmation and proposed.confirmation_id
        assert db.scalar(select(AssetNote)) is None  # nothing executed yet
        model_view = proposed.as_tool_result()
        assert model_view["status"] == "needs_confirmation"

        outcome = approve_confirmation(db, proposed.confirmation_id)
        assert outcome.ok
        db.expire_all()
        assert db.scalar(select(AssetNote)).source == "ai"
        row = db.get(AgentToolCall, proposed.confirmation_id)
        assert row.status == "approved" and row.resolved_at is not None

    def test_reject_executes_nothing(self, seeded, db):
        proposed = run(db, "write_research_note",
                       {"symbol": "AAPL", "markdown": "memo"}, confirm_writes=True)
        reject_confirmation(db, proposed.confirmation_id)
        assert db.scalar(select(AssetNote)) is None
        row = db.get(AgentToolCall, proposed.confirmation_id)
        assert row.status == "rejected"

    def test_double_resolve_conflicts(self, seeded, db):
        proposed = run(db, "write_research_note",
                       {"symbol": "AAPL", "markdown": "memo"}, confirm_writes=True)
        approve_confirmation(db, proposed.confirmation_id)
        with pytest.raises(ConfirmationError) as excinfo:
            approve_confirmation(db, proposed.confirmation_id)
        assert excinfo.value.status_code == 409

    def test_read_tools_never_gated(self, seeded, db):
        outcome = run(db, "get_asset_overview", {"symbol": "AAPL"}, confirm_writes=True)
        assert outcome.ok and not outcome.needs_confirmation
