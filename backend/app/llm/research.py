"""Agentic research tasks (spec §13.3): on-demand deep-dives and the nightly
memo run over top new candidates.

Runner pattern: the conversation row IS the job row — failures become
status='error' + error text, never a Celery exception. Task conversations run
autonomous (no confirmation cards to click in a worker) but with the tool set
pinned to read tier + write_research_note only.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import get_logger
from app.llm.budget import BudgetExceeded, ensure_budget
from app.llm.prompts import GUARDRAILS
from app.llm.tooldefs import TOOLS, ToolDef

log = get_logger(__name__)

DEEP_DIVE_TOOL_NAMES = (
    "get_asset_overview",
    "get_ohlcv",
    "get_fundamentals",
    "get_news",
    "backtest_signal",
    "write_research_note",  # the only write a research task can perform
)


def deep_dive_tools() -> list[ToolDef]:
    return [TOOLS[name] for name in DEEP_DIVE_TOOL_NAMES]


def deep_dive_system() -> str:
    return f"""You run scheduled research tasks inside Plutus, the user's \
self-hosted investment hub. You produce ONE research memo per task by calling \
write_research_note exactly once at the end. Nobody is watching live — never \
ask questions, never wait for approval.

{GUARDRAILS}"""


def deep_dive_prompt(symbol: str) -> str:
    today = datetime.now(UTC).date().isoformat()
    return f"""Research {symbol} and write a memo. Work through these steps:

1. get_asset_overview — where the price sits vs its moving averages, RSI, \
52-week range, valuation ratios.
2. get_fundamentals — revenue/earnings direction, margins, debt. Skip \
gracefully if empty.
3. get_news (days=14) — recent headlines; note anything that plausibly \
explains the price action.
4. backtest_signal — test the 1-2 conditions most relevant to what you found \
(e.g. the RSI level or a moving-average cross it just made).
5. write_research_note with title "AI research memo — {symbol} — {today}". \
Structure: **Snapshot** (3-4 numbers that matter), **Fundamentals**, \
**News context**, **Signal history** (what happened after similar setups), \
**What would change this view**. Quote real numbers with dates from the \
tools. Keep it under 400 words."""


def _memo_note_id(session, asset_id: int, since: datetime) -> int | None:
    from app.models import AssetNote

    return session.scalar(
        select(AssetNote.id)
        .where(
            AssetNote.asset_id == asset_id,
            AssetNote.source == "ai",
            AssetNote.created_at >= since,
        )
        .order_by(AssetNote.id.desc())
        .limit(1)
    )


def run_deep_dive(conversation_id: int) -> int:
    """Execute one queued deep-dive task conversation. Returns the note id (0 if none)."""
    from app.models import AgentConversation, Asset, Candidate

    session = SessionLocal()
    try:
        conversation = session.get(AgentConversation, conversation_id)
        if conversation is None or conversation.kind != "task":
            log.warning("deep_dive_missing_conversation", conversation_id=conversation_id)
            return 0
        meta = conversation.task_meta or {}
        asset = session.get(Asset, int(meta.get("asset_id") or 0))
        if asset is None:
            conversation.status = "error"
            conversation.error = "task_meta.asset_id missing or unknown"
            session.commit()
            return 0

        conversation.status = "running"
        conversation.autonomous = True  # worker context: no one to click a card
        session.commit()
        started_at = datetime.now(UTC)

        try:
            ensure_budget(session)
        except BudgetExceeded as exc:
            conversation.status = "error"
            conversation.error = str(exc)
            session.commit()
            log.warning("deep_dive_budget_exceeded", conversation_id=conversation_id)
            return 0

        error = _run_turn(conversation_id, deep_dive_prompt(asset.symbol))

        session.expire_all()
        conversation = session.get(AgentConversation, conversation_id)
        note_id = _memo_note_id(session, asset.id, started_at)
        if error is not None:
            conversation.status = "error"
            conversation.error = error
        elif note_id is None:
            conversation.status = "error"
            conversation.error = "agent finished without writing a memo"
        else:
            conversation.status = "done"
            candidate_id = meta.get("candidate_id")
            if candidate_id:
                candidate = session.get(Candidate, int(candidate_id))
                if candidate is not None:
                    candidate.context = {**(candidate.context or {}),
                                         "memo_note_id": note_id}
        session.commit()
        return note_id or 0
    finally:
        session.close()


def _run_turn(conversation_id: int, prompt: str) -> str | None:
    """Drive one agent turn to completion; returns the error message, if any."""
    from app.llm.loop import run_agent_turn

    async def _consume() -> str | None:
        last_error: str | None = None
        finished = False
        async for event in run_agent_turn(
            conversation_id, prompt,
            system=deep_dive_system(), tools=deep_dive_tools(), source="task",
        ):
            if event.type == "error":
                last_error = str(event.data.get("message"))
            elif event.type == "done":
                finished = True
        if not finished and last_error is None:
            last_error = "agent turn ended without completing"
        return last_error

    return asyncio.run(_consume())


def run_nightly_memos() -> list[int]:
    """Generate memos for last night's best unreviewed candidates (spec §13.3)."""
    from sqlalchemy import func

    from app.models import AgentConversation, Asset, Candidate, Mandate

    settings = get_settings()
    limit = settings.agent_nightly_memo_limit
    session = SessionLocal()
    try:
        threshold = func.coalesce(Mandate.notify_min_score, Mandate.min_score)
        rows = session.execute(
            select(Candidate, Asset.symbol)
            .join(Mandate, Mandate.id == Candidate.mandate_id)
            .join(Asset, Asset.id == Candidate.asset_id)
            .where(
                Candidate.status == "new",
                Candidate.created_at >= datetime.now(UTC) - timedelta(hours=24),
                Candidate.score >= threshold,
                ~Candidate.context.has_key("memo_note_id"),
            )
            .order_by(Candidate.score.desc())
            .limit(limit)
        ).all()

        conversation_ids: list[tuple[int, str]] = []
        for candidate, symbol in rows:
            conversation = AgentConversation(
                kind="task",
                status="queued",
                autonomous=True,
                title=f"Nightly memo — {symbol}",
                task_meta={
                    "asset_id": candidate.asset_id,
                    "candidate_id": candidate.id,
                    "trigger": "nightly",
                },
            )
            session.add(conversation)
            session.flush()
            conversation_ids.append((conversation.id, symbol))
        session.commit()
    finally:
        session.close()

    written: list[int] = []
    skipped_budget = 0
    for conversation_id, symbol in conversation_ids:
        check = SessionLocal()
        try:
            try:
                ensure_budget(check)
            except BudgetExceeded:
                skipped_budget += 1
                log.warning("memo_budget_exceeded", conversation_id=conversation_id,
                            symbol=symbol)
                conversation = check.get(AgentConversation, conversation_id)
                conversation.status = "error"
                conversation.error = "skipped: daily token budget exceeded"
                check.commit()
                continue
        finally:
            check.close()
        note_id = run_deep_dive(conversation_id)
        if note_id:
            written.append(note_id)

    if written:
        _notify_memos_ready(written)
    log.info("nightly_memos_done", written=len(written), skipped_budget=skipped_budget)
    return written


def _notify_memos_ready(note_ids: list[int]) -> None:
    from app.discovery.notify import deliver

    session = SessionLocal()
    try:
        from app.briefing.morning import is_enabled as brief_enabled

        if brief_enabled(session):
            # the 08:45 morning brief lists overnight memos — skip the extra ping
            return
        count = len(note_ids)
        plural = "s" if count != 1 else ""
        deliver(
            session,
            kind="memo",
            subject=f"Plutus: {count} AI research memo{plural} ready",
            body=(
                f"{count} research memo{plural} were generated for last night's top "
                "candidates. Open the Inbox to read them alongside each candidate's "
                "evidence.\n\nAI-generated, informational only. Not investment advice."
            ),
            meta={"note_ids": note_ids},
        )
        session.commit()
    finally:
        session.close()
