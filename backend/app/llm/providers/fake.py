"""Scripted provider for tests: pops one LLMResponse per chat() call and
records everything it was asked."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.llm.base import ChatProvider, LLMError
from app.llm.tooldefs import ToolDef
from app.llm.types import LLMResponse, Message


@dataclass
class FakeProvider(ChatProvider):
    script: list[LLMResponse] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)
    name: str = "fake"
    model: str = "fake-model"

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        self.calls.append(
            {
                "messages": list(messages),
                "tools": [tool.name for tool in tools or []],
                "system": system,
            }
        )
        if not self.script:
            raise LLMError("FakeProvider script exhausted")
        return self.script.pop(0)
