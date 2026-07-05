"""Agent chat surface: conversations, the SSE message stream, confirmation
resolution, and the sidecar's tool-execution callback."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.llm.executor import (
    ConfirmationError,
    approve_confirmation,
    execute_tool,
    reject_confirmation,
)
from app.llm.loop import run_agent_turn
from app.models import AgentConversation, AgentMessage, AgentToolCall
from app.schemas.agent import (
    ConfirmationOut,
    ConfirmationResolutionOut,
    ConversationDetailOut,
    ConversationIn,
    ConversationOut,
    ConversationPatch,
    MessageOut,
    SendMessageIn,
    ToolExecuteIn,
)

router = APIRouter(prefix="/agent", tags=["agent"])

HEARTBEAT_SECONDS = 15.0


def _get_conversation_or_404(db: Session, conversation_id: int) -> AgentConversation:
    conversation = db.get(AgentConversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conversation


@router.post("/conversations", response_model=ConversationOut, status_code=201)
def create_conversation(body: ConversationIn, db: Session = Depends(get_db)):
    conversation = AgentConversation(kind="chat", title=body.title)
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(
    db: Session = Depends(get_db),
    kind: str = Query(default="chat", pattern="^(chat|task|translate)$"),
    limit: int = Query(default=50, ge=1, le=200),
):
    return db.scalars(
        select(AgentConversation)
        .where(AgentConversation.kind == kind)
        .order_by(AgentConversation.updated_at.desc())
        .limit(limit)
    ).all()


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailOut)
def get_conversation(conversation_id: int, db: Session = Depends(get_db)):
    conversation = _get_conversation_or_404(db, conversation_id)
    messages = db.scalars(
        select(AgentMessage)
        .where(AgentMessage.conversation_id == conversation.id)
        .order_by(AgentMessage.id)
    ).all()
    pending = db.scalars(
        select(AgentToolCall)
        .where(
            AgentToolCall.conversation_id == conversation.id,
            AgentToolCall.status == "pending_confirmation",
        )
        .order_by(AgentToolCall.id)
    ).all()
    return ConversationDetailOut(
        conversation=ConversationOut.model_validate(conversation),
        messages=[MessageOut.model_validate(m) for m in messages],
        pending_confirmations=[ConfirmationOut.model_validate(c) for c in pending],
    )


@router.patch("/conversations/{conversation_id}", response_model=ConversationOut)
def patch_conversation(
    conversation_id: int, body: ConversationPatch, db: Session = Depends(get_db)
):
    conversation = _get_conversation_or_404(db, conversation_id)
    if body.title is not None:
        conversation.title = body.title
    if body.autonomous is not None:
        conversation.autonomous = body.autonomous
    db.commit()
    db.refresh(conversation)
    return conversation


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: int, db: Session = Depends(get_db)):
    conversation = _get_conversation_or_404(db, conversation_id)
    db.delete(conversation)  # messages cascade; audit rows keep a NULL conversation
    db.commit()


def _encode(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, default=str)}\n\n"


async def _sse_with_heartbeat(conversation_id: int, content: str):
    """The agent turn as SSE frames, with comment pings so proxies keep the
    connection open during long provider calls."""
    turn = run_agent_turn(conversation_id, content)
    iterator = turn.__aiter__()
    while True:
        next_event = asyncio.ensure_future(iterator.__anext__())
        while True:
            done, _ = await asyncio.wait({next_event}, timeout=HEARTBEAT_SECONDS)
            if done:
                break
            yield ": ping\n\n"
        try:
            event = next_event.result()
        except StopAsyncIteration:
            return
        yield _encode(event.type, event.data)
        if event.type in ("done", "error"):
            # the loop generator finalizes (closes its session) on return
            await turn.aclose()
            return


@router.post("/conversations/{conversation_id}/messages")
def send_message(
    conversation_id: int, body: SendMessageIn, db: Session = Depends(get_db)
):
    conversation = _get_conversation_or_404(db, conversation_id)
    if conversation.kind != "chat":
        raise HTTPException(status_code=409, detail="only chat conversations accept messages")
    return StreamingResponse(
        _sse_with_heartbeat(conversation.id, body.content),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/confirmations/{confirmation_id}/approve",
             response_model=ConfirmationResolutionOut)
def approve(confirmation_id: int, db: Session = Depends(get_db)):
    try:
        outcome = approve_confirmation(db, confirmation_id)
    except ConfirmationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return ConfirmationResolutionOut(
        ok=outcome.ok,
        status="approved" if outcome.ok else "error",
        result_summary=outcome.summary,
        error=outcome.error,
        result=outcome.result,
    )


@router.post("/confirmations/{confirmation_id}/reject",
             response_model=ConfirmationResolutionOut)
def reject(confirmation_id: int, db: Session = Depends(get_db)):
    try:
        reject_confirmation(db, confirmation_id)
    except ConfirmationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return ConfirmationResolutionOut(ok=True, status="rejected")


@router.post("/tools/execute")
def execute_tool_callback(body: ToolExecuteIn, db: Session = Depends(get_db)):
    """The sidecar's handlers call back here so gating/auditing is identical to
    the Python loop. Protected by the same bearer token as every /api/v1 route."""
    confirm_writes = True
    if body.conversation_id is not None:
        conversation = db.get(AgentConversation, body.conversation_id)
        if conversation is not None:
            confirm_writes = not conversation.autonomous
    outcome = execute_tool(
        db,
        body.name,
        body.arguments,
        source="app",
        conversation_id=body.conversation_id,
        confirm_writes=confirm_writes,
    )
    payload = outcome.as_tool_result()
    # extras the sidecar forwards in its tool_result SSE event
    payload["_meta"] = {
        "ok": outcome.ok,
        "needs_confirmation": outcome.needs_confirmation,
        "confirmation_id": outcome.confirmation_id,
        "summary": outcome.summary,
        "name": outcome.name,
    }
    return payload
