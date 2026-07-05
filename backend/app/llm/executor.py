"""The single tool-execution chokepoint every agent surface goes through.

Whether a call originates from the in-app chat loop, the claude-subscription
sidecar callback, a Celery research task, or the MCP server, it lands here:
schema validation → tier gate → confirmation gate → handler → audit row.

Errors become RESULTS, not exceptions — the model reads them and self-corrects;
a tool call can never 500 a surface.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jsonschema
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.llm.tooldefs import TOOLS, ToolDef, ToolInputError

log = get_logger(__name__)

CONFIRMATION_TTL = timedelta(hours=24)
# safety net above every tool's own output cap (500 OHLCV rows ≈ 25k chars)
MAX_RESULT_CHARS = 60_000


@dataclass(frozen=True)
class ToolOutcome:
    name: str
    ok: bool
    tier: str = "read"
    result: Any = None
    error: str | None = None
    needs_confirmation: bool = False
    confirmation_id: int | None = None
    summary: str | None = None
    audit_id: int | None = None
    extra: dict = field(default_factory=dict)

    def as_tool_result(self) -> dict:
        """The JSON the model sees as the tool's return value."""
        if self.needs_confirmation:
            return {
                "status": "needs_confirmation",
                "confirmation_id": self.confirmation_id,
                "summary": self.summary,
                "note": (
                    "This write action is proposed, not executed. The user sees a "
                    "confirmation card — tell them what you proposed and wait; do "
                    "not retry the call."
                ),
            }
        if not self.ok:
            return {"status": "error", "error": self.error}
        return {"status": "ok", "result": self.result}


def _audit(
    session: Session,
    *,
    tool: ToolDef | None,
    name: str,
    arguments: dict,
    source: str,
    conversation_id: int | None,
    status: str,
    result_summary: str | None = None,
    error: str | None = None,
) -> int:
    from app.models import AgentToolCall

    row = AgentToolCall(
        conversation_id=conversation_id,
        source=source,
        tier=tool.tier if tool else "read",
        name=name,
        arguments=arguments,
        status=status,
        result_summary=result_summary,
        error=error,
        resolved_at=None if status == "pending_confirmation" else datetime.now(UTC),
    )
    session.add(row)
    session.flush()
    return row.id


def _clip_result(result: Any) -> Any:
    encoded = json.dumps(result, default=str)
    if len(encoded) <= MAX_RESULT_CHARS:
        return result
    return {
        "truncated": True,
        "note": f"result was {len(encoded)} chars; ask for a narrower slice",
        "head": encoded[:MAX_RESULT_CHARS],
    }


def execute_tool(
    session: Session,
    name: str,
    arguments: dict | None,
    *,
    source: Literal["app", "task", "mcp"],
    conversation_id: int | None = None,
    allowed_tier: Literal["read", "write"] = "write",
    allowed_tools: frozenset[str] | None = None,
    confirm_writes: bool = False,
) -> ToolOutcome:
    arguments = arguments or {}
    tool = TOOLS.get(name)

    if tool is None:
        _audit(session, tool=None, name=name, arguments=arguments, source=source,
               conversation_id=conversation_id, status="error", error="unknown tool")
        session.commit()
        return ToolOutcome(
            name=name, ok=False,
            error=f"unknown tool '{name}' — available: {sorted(TOOLS)}",
        )

    if allowed_tools is not None and name not in allowed_tools:
        error = f"tool '{name}' is not available in this context"
        audit_id = _audit(session, tool=tool, name=name, arguments=arguments, source=source,
                          conversation_id=conversation_id, status="error", error=error)
        session.commit()
        return ToolOutcome(name=name, ok=False, tier=tool.tier, error=error, audit_id=audit_id)

    if tool.tier == "write" and allowed_tier == "read":
        error = "this surface is read-only (MCP_TOOL_TIER=read) — write tools are disabled"
        audit_id = _audit(session, tool=tool, name=name, arguments=arguments, source=source,
                          conversation_id=conversation_id, status="error", error=error)
        session.commit()
        return ToolOutcome(name=name, ok=False, tier=tool.tier, error=error, audit_id=audit_id)

    try:
        jsonschema.validate(arguments, tool.schema)
    except jsonschema.ValidationError as exc:
        error = f"invalid arguments: {exc.message}"
        audit_id = _audit(session, tool=tool, name=name, arguments=arguments, source=source,
                          conversation_id=conversation_id, status="error", error=error)
        session.commit()
        return ToolOutcome(name=name, ok=False, tier=tool.tier, error=error, audit_id=audit_id)

    if tool.tier == "write" and confirm_writes:
        summary = f"{name}({json.dumps(arguments, default=str)[:300]})"
        audit_id = _audit(session, tool=tool, name=name, arguments=arguments, source=source,
                          conversation_id=conversation_id, status="pending_confirmation",
                          result_summary=summary)
        session.commit()
        return ToolOutcome(
            name=name, ok=True, tier=tool.tier, needs_confirmation=True,
            confirmation_id=audit_id, summary=summary, audit_id=audit_id,
        )

    return _run_handler(session, tool, arguments, source=source,
                        conversation_id=conversation_id)


def _run_handler(
    session: Session,
    tool: ToolDef,
    arguments: dict,
    *,
    source: str,
    conversation_id: int | None,
    audit_row_id: int | None = None,
) -> ToolOutcome:
    from app.models import AgentToolCall

    try:
        result = tool.handler(session, arguments)
        summary = tool.summarize(arguments, result)
        status, error = "done", None
    except ToolInputError as exc:
        result, summary, status, error = None, None, "error", str(exc)
    except Exception as exc:  # noqa: BLE001 — a tool must never crash a surface
        log.error("tool_handler_failed", tool=tool.name, error=str(exc))
        session.rollback()
        result, summary, status = None, None, "error"
        error = f"{tool.name} failed: {exc}"

    if audit_row_id is not None:
        row = session.get(AgentToolCall, audit_row_id)
        if row is not None:
            row.status = "approved" if status == "done" else "error"
            row.result_summary = summary or row.result_summary
            row.error = error
            row.resolved_at = datetime.now(UTC)
            audit_id = row.id
        else:  # pragma: no cover — the confirmation row vanished mid-approve
            audit_id = _audit(session, tool=tool, name=tool.name, arguments=arguments,
                              source=source, conversation_id=conversation_id,
                              status=status, result_summary=summary, error=error)
    else:
        audit_id = _audit(session, tool=tool, name=tool.name, arguments=arguments,
                          source=source, conversation_id=conversation_id,
                          status=status, result_summary=summary, error=error)
    session.commit()

    if error is not None:
        return ToolOutcome(name=tool.name, ok=False, tier=tool.tier, error=error,
                           audit_id=audit_id)
    return ToolOutcome(name=tool.name, ok=True, tier=tool.tier,
                       result=_clip_result(result), summary=summary, audit_id=audit_id)


# --- confirmation resolution -------------------------------------------------------


class ConfirmationError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _pending_row(session: Session, confirmation_id: int):
    from app.models import AgentToolCall

    row = session.get(AgentToolCall, confirmation_id)
    if row is None:
        raise ConfirmationError(404, "confirmation not found")
    if row.status == "pending_confirmation":
        age = datetime.now(UTC) - row.created_at
        if age > CONFIRMATION_TTL:
            row.status = "expired"
            row.resolved_at = datetime.now(UTC)
            session.commit()
            raise ConfirmationError(409, "confirmation expired (older than 24h)")
        return row
    raise ConfirmationError(409, f"confirmation already {row.status}")


def approve_confirmation(session: Session, confirmation_id: int) -> ToolOutcome:
    row = _pending_row(session, confirmation_id)
    tool = TOOLS.get(row.name)
    if tool is None:  # pragma: no cover — tool removed between propose and approve
        raise ConfirmationError(409, f"tool '{row.name}' no longer exists")
    outcome = _run_handler(
        session, tool, row.arguments,
        source=row.source, conversation_id=row.conversation_id,
        audit_row_id=row.id,
    )
    _append_confirmation_message(session, row, outcome, approved=True)
    return outcome


def reject_confirmation(session: Session, confirmation_id: int) -> None:
    row = _pending_row(session, confirmation_id)
    row.status = "rejected"
    row.resolved_at = datetime.now(UTC)
    outcome = ToolOutcome(name=row.name, ok=False, tier=row.tier,
                          error="the user rejected this action")
    _append_confirmation_message(session, row, outcome, approved=False)
    session.commit()


def _append_confirmation_message(session, row, outcome: ToolOutcome, *, approved: bool) -> None:
    """Record the resolution in the conversation so the model's NEXT turn sees it."""
    if row.conversation_id is None:
        return
    from app.models import AgentMessage

    result = (
        {"status": "ok", "result": outcome.result, "note": "the user approved this action"}
        if approved and outcome.ok
        else {"status": "error", "error": outcome.error}
    )
    session.add(
        AgentMessage(
            conversation_id=row.conversation_id,
            role="tool",
            tool_call_id=f"confirmation_{row.id}",
            tool_name=row.name,
            tool_result=result,
        )
    )
    session.flush()
