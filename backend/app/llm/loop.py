"""The agent turn: one user message in, a stream of AgentEvents out.

Two loop modes converge on the same tool chokepoint (`executor.execute_tool`):

- ChatProvider (anthropic-api, openai-compat family): Python owns the loop —
  up to `agent_max_tool_iterations` provider rounds, tools executed between
  rounds, results appended as role='tool' messages.
- AgentLoopProvider (claude-subscription sidecar): the CLI owns the loop and
  streams normalized events; tools still execute in Python via the sidecar's
  HTTP callback, so gating/auditing is identical.

Everything is persisted as it happens; a page reload rebuilds the transcript
from agent_messages + agent_tool_calls alone.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import get_logger
from app.llm.base import AgentLoopProvider, LLMError, ProviderUnavailableError
from app.llm.budget import BudgetExceeded, ensure_budget
from app.llm.executor import execute_tool
from app.llm.prompts import chat_system
from app.llm.tooldefs import ToolDef, tools_for_tier
from app.llm.types import (
    AgentEvent,
    LLMResponse,
    Message,
    ResponseDone,
    TextDelta,
    ToolCallReady,
    Usage,
)

log = get_logger(__name__)

HISTORY_LIMIT = 60  # transcript rows fed back to the model per turn
EXHAUSTED_NUDGE = (
    "Tool budget for this turn is exhausted. Answer now with what you have; "
    "say explicitly what you could not verify."
)


def to_provider_messages(rows) -> list[Message]:
    """DB AgentMessage rows → provider-neutral messages, oldest first."""
    from app.llm.types import ToolCallRequest

    messages: list[Message] = []
    for row in rows:
        if row.role == "assistant":
            calls = [
                ToolCallRequest(id=c["id"], name=c["name"], arguments=c.get("arguments") or {})
                for c in (row.tool_calls or [])
            ]
            messages.append(Message(role="assistant", content=row.content,
                                    tool_calls=calls or None))
        elif row.role == "tool":
            messages.append(
                Message(role="tool", tool_call_id=row.tool_call_id,
                        tool_name=row.tool_name, tool_result=row.tool_result)
            )
        else:
            messages.append(Message(role=row.role, content=row.content))
    return messages


async def run_agent_turn(
    conversation_id: int,
    user_text: str,
    *,
    system: str | None = None,
    tools: list[ToolDef] | None = None,
    source: str = "app",
) -> AsyncIterator[AgentEvent]:
    from app.llm.providers import get_provider
    from app.models import AgentConversation, AgentMessage

    session = SessionLocal()
    try:
        conversation = session.get(AgentConversation, conversation_id)
        if conversation is None:
            yield AgentEvent("error", {"message": "conversation not found"})
            return

        session.add(AgentMessage(conversation_id=conversation.id, role="user",
                                 content=user_text))
        if conversation.title is None and conversation.kind == "chat":
            conversation.title = user_text[:80]
        session.commit()

        try:
            ensure_budget(session)
        except BudgetExceeded as exc:
            yield AgentEvent("error", {"message": str(exc)})
            return

        try:
            provider = get_provider(session)
        except LLMError as exc:
            yield AgentEvent("error", {"message": str(exc)})
            return

        conversation.provider = provider.name
        conversation.model = provider.model or conversation.model
        session.commit()

        yield AgentEvent("start", {
            "conversation_id": conversation.id,
            "provider": provider.name,
            "model": provider.model,
        })

        system = system or chat_system()
        tools = tools if tools is not None else tools_for_tier("write")

        if isinstance(provider, AgentLoopProvider):
            async for event in _sidecar_turn(
                session, conversation, provider, user_text, system, tools
            ):
                yield event
        else:
            async for event in _python_loop_turn(
                session, conversation, provider, system, tools, source
            ):
                yield event
    except (LLMError, ProviderUnavailableError) as exc:
        yield AgentEvent("error", {"message": str(exc)})
    except Exception as exc:  # noqa: BLE001 — the stream must end with an event
        log.error("agent_turn_failed", conversation_id=conversation_id, error=str(exc))
        yield AgentEvent("error", {"message": f"agent turn failed: {exc}"})
    finally:
        session.close()


async def _python_loop_turn(
    session, conversation, provider, system: str, tools: list[ToolDef], source: str
) -> AsyncIterator[AgentEvent]:
    from app.models import AgentMessage

    max_iterations = get_settings().agent_max_tool_iterations
    turn_usage = Usage()
    final_message_id: int | None = None

    for iteration in range(max_iterations + 1):
        history = session.scalars(
            select(AgentMessage)
            .where(AgentMessage.conversation_id == conversation.id)
            .order_by(AgentMessage.id.desc())
            .limit(HISTORY_LIMIT)
        ).all()
        messages = to_provider_messages(reversed(history))

        exhausted = iteration == max_iterations
        if exhausted:
            messages.append(Message(role="user", content=EXHAUSTED_NUDGE))

        response: LLMResponse | None = None
        async for event in provider.chat_stream(
            messages, tools=None if exhausted else tools, system=system
        ):
            if isinstance(event, TextDelta):
                yield AgentEvent("text_delta", {"text": event.text})
            elif isinstance(event, ResponseDone):
                response = event.response
            elif isinstance(event, ToolCallReady):
                pass  # announced below, in order, once the response is complete
        assert response is not None, "provider stream ended without ResponseDone"

        turn_usage = turn_usage + response.usage
        assistant_row = AgentMessage(
            conversation_id=conversation.id,
            role="assistant",
            content=response.text,
            tool_calls=[
                {"id": c.id, "name": c.name, "arguments": c.arguments}
                for c in response.tool_calls
            ] or None,
            provider=provider.name,
            model=provider.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
        session.add(assistant_row)
        session.commit()
        final_message_id = assistant_row.id

        if not response.tool_calls:
            break

        for call in response.tool_calls:
            yield AgentEvent("tool_call", {
                "tool_call_id": call.id, "name": call.name, "arguments": call.arguments,
            })
            outcome = execute_tool(
                session, call.name, call.arguments,
                source=source, conversation_id=conversation.id,
                confirm_writes=not conversation.autonomous,
            )
            model_view = outcome.as_tool_result()
            session.add(AgentMessage(
                conversation_id=conversation.id, role="tool",
                tool_call_id=call.id, tool_name=call.name, tool_result=model_view,
            ))
            session.commit()
            if outcome.needs_confirmation:
                yield AgentEvent("confirmation_required", {
                    "confirmation_id": outcome.confirmation_id,
                    "tool_call_id": call.id,
                    "name": call.name,
                    "arguments": call.arguments,
                    "summary": outcome.summary,
                })
            else:
                yield AgentEvent("tool_result", {
                    "tool_call_id": call.id, "name": call.name, "ok": outcome.ok,
                    "summary": outcome.summary, "error": outcome.error,
                    "result": _preview(outcome.result),
                })

        try:
            ensure_budget(session)
        except BudgetExceeded as exc:
            yield AgentEvent("error", {"message": str(exc)})
            return

    yield AgentEvent("done", {
        "message_id": final_message_id,
        "input_tokens": turn_usage.input_tokens,
        "output_tokens": turn_usage.output_tokens,
    })


async def _sidecar_turn(
    session, conversation, provider, user_text: str, system: str, tools: list[ToolDef]
) -> AsyncIterator[AgentEvent]:
    """Delegate the loop to the sidecar; persist what streams back so the
    transcript rebuild matches the python-loop shape."""
    from app.models import AgentMessage

    max_turns = get_settings().agent_max_tool_iterations
    text_parts: list[str] = []
    tool_calls: list[dict] = []

    async for event in provider.run_loop(
        system=system, user_message=user_text, tools=tools,
        conversation_id=conversation.id,
        session_id=conversation.sidecar_session_id, max_turns=max_turns,
    ):
        if event.type == "text_delta":
            text_parts.append(event.data.get("text", ""))
            yield event
        elif event.type == "tool_call":
            tool_calls.append({
                "id": event.data.get("tool_call_id"),
                "name": event.data.get("name"),
                "arguments": event.data.get("arguments") or {},
            })
            yield event
        elif event.type == "tool_result":
            # execution + audit already happened via the /agent/tools/execute callback
            yield event
            if event.data.get("needs_confirmation"):
                yield AgentEvent("confirmation_required", {
                    "confirmation_id": event.data.get("confirmation_id"),
                    "tool_call_id": event.data.get("tool_call_id"),
                    "name": event.data.get("name"),
                    "arguments": event.data.get("arguments") or {},
                    "summary": event.data.get("summary"),
                })
        elif event.type == "done":
            usage = event.data.get("usage") or {}
            input_tokens = int(usage.get("input_tokens") or 0)
            output_tokens = int(usage.get("output_tokens") or 0)
            row = AgentMessage(
                conversation_id=conversation.id,
                role="assistant",
                content="".join(text_parts) or None,
                tool_calls=tool_calls or None,
                provider=provider.name,
                model=event.data.get("model") or provider.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            session.add(row)
            if event.data.get("session_id"):
                conversation.sidecar_session_id = event.data["session_id"]
            session.commit()
            yield AgentEvent("done", {
                "message_id": row.id,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            })
        elif event.type == "error":
            yield event


def _preview(result, limit: int = 2000):
    """Tool results streamed to the UI stay small; the model gets the full one."""
    if result is None:
        return None
    encoded = json.dumps(result, default=str)
    if len(encoded) <= limit:
        return result
    return {"preview": encoded[:limit], "truncated": True}
