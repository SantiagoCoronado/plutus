"""Provider protocols. Two shapes exist on purpose:

- `ChatProvider`: stateless message API (anthropic-api + the OpenAI-compatible
  family). The Python side owns the tool loop.
- `AgentLoopProvider`: the claude-subscription sidecar, where the Claude CLI
  runs the loop itself and streams normalized events back; tools still execute
  in Python via the /agent/tools/execute callback.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from app.llm.tooldefs import ToolDef
from app.llm.types import (
    AgentEvent,
    LLMResponse,
    Message,
    ProviderEvent,
    ResponseDone,
    TextDelta,
    ToolCallReady,
)


class LLMError(Exception):
    """Provider returned an error the caller should surface (bad key, 4xx/5xx)."""


class ProviderUnavailableError(LLMError):
    """The provider can't be reached at all (sidecar down, connection refused)."""


class ChatProvider(ABC):
    name: str = "base"
    model: str = ""

    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse: ...

    async def chat_stream(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> AsyncIterator[ProviderEvent]:
        """Default: one chat() call re-emitted as events, so non-streaming
        adapters still serve the chat UI (whole-message chunks)."""
        response = await self.chat(messages, tools, system, max_tokens)
        if response.text:
            yield TextDelta(response.text)
        for call in response.tool_calls:
            yield ToolCallReady(call)
        yield ResponseDone(response)


class AgentLoopProvider(ABC):
    name: str = "base-loop"
    model: str = ""

    @abstractmethod
    def run_loop(
        self,
        *,
        system: str,
        user_message: str,
        tools: list[ToolDef],
        conversation_id: int,
        session_id: str | None,
        max_turns: int,
    ) -> AsyncIterator[AgentEvent]: ...


def tool_wire_format(tool: ToolDef) -> dict:
    """The {name, description, input_schema} triple every surface serializes."""
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.schema,
    }
