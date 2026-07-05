"""Provider-neutral message/response/event shapes.

Every provider adapter normalizes to these; the loop, persistence, and the
SSE encoder never see a provider-specific format.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class ToolCallRequest:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant", "tool", "system"]
    content: str | None = None
    tool_calls: list[ToolCallRequest] | None = None  # assistant messages
    tool_call_id: str | None = None                  # tool messages
    tool_name: str | None = None
    tool_result: dict[str, Any] | None = None


@dataclass(frozen=True)
class LLMResponse:
    text: str | None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    stop_reason: Literal["end", "tool_use", "max_tokens"] = "end"


# --- provider stream events (Python-loop providers) --------------------------------


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolCallReady:
    call: ToolCallRequest


@dataclass(frozen=True)
class ResponseDone:
    response: LLMResponse


ProviderEvent = TextDelta | ToolCallReady | ResponseDone


# --- normalized agent events (what surfaces stream / SSE serializes) ---------------


@dataclass(frozen=True)
class AgentEvent:
    type: Literal[
        "start",
        "text_delta",
        "tool_call",
        "tool_result",
        "confirmation_required",
        "done",
        "error",
    ]
    data: dict[str, Any] = field(default_factory=dict)
