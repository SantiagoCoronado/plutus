"""Driver for the claude-subscription Node sidecar (spec §13.1).

The sidecar wraps @anthropic-ai/claude-agent-sdk: the Claude CLI runs the tool
loop internally on the user's subscription auth, its SDK tool handlers POST
back into /api/v1/agent/tools/execute, and this driver just re-emits the
sidecar's SSE as normalized AgentEvents.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from app.core.logging import get_logger
from app.llm.base import (
    AgentLoopProvider,
    LLMError,
    ProviderUnavailableError,
    tool_wire_format,
)
from app.llm.tooldefs import ToolDef
from app.llm.types import AgentEvent

log = get_logger(__name__)

KNOWN_EVENTS = frozenset(
    {"start", "text_delta", "tool_call", "tool_result", "done", "error"}
)


class ClaudeSidecarProvider(AgentLoopProvider):
    name = "claude-subscription"

    def __init__(self, *, base_url: str, model: str = "", timeout: float = 600.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    async def health(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"{self.base_url}/health")
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                f"claude sidecar not reachable at {self.base_url} — "
                "start the agent-sidecar service or switch provider in Settings"
            ) from exc
        return response.json()

    async def run_loop(
        self,
        *,
        system: str,
        user_message: str,
        tools: list[ToolDef],
        conversation_id: int,
        session_id: str | None,
        max_turns: int,
    ) -> AsyncIterator[AgentEvent]:
        body = {
            "system": system,
            "user_message": user_message,
            "session_id": session_id,
            "conversation_id": conversation_id,
            "tools": [tool_wire_format(tool) for tool in tools],
            "max_turns": max_turns,
            "model": self.model or None,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream(
                    "POST", f"{self.base_url}/chat/stream", json=body
                ) as response:
                    if response.status_code != 200:
                        detail = (await response.aread()).decode()[:300]
                        raise LLMError(f"sidecar returned {response.status_code}: {detail}")
                    async for event in self._parse_sse(response):
                        yield event
        except httpx.ConnectError as exc:
            raise ProviderUnavailableError(
                f"claude sidecar not reachable at {self.base_url} — "
                "start the agent-sidecar service or switch provider in Settings"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"sidecar stream failed: {exc}") from exc

    async def _parse_sse(self, response) -> AsyncIterator[AgentEvent]:
        event_name = "message"
        async for line in response.aiter_lines():
            line = line.rstrip("\n")
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                try:
                    data = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    log.warning("sidecar_bad_event", raw=line[:200])
                    continue
                if event_name in KNOWN_EVENTS:
                    yield AgentEvent(type=event_name, data=data)  # type: ignore[arg-type]
                event_name = "message"
