"""One adapter, four providers: openai / openrouter / google (Gemini's
OpenAI-compatible endpoint) / ollama — base URL + key + default model differ,
the Chat Completions wire format is shared.

chat_stream deliberately uses the base-class fallback (one chat() call
re-emitted): streamed tool-call deltas behave differently across these
endpoints (Gemini and ollama are the usual offenders), and buffered whole
messages are indistinguishable in this UI for research-sized replies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx

from app.core.logging import get_logger
from app.llm.base import ChatProvider, LLMError
from app.llm.tooldefs import ToolDef
from app.llm.types import LLMResponse, Message, ToolCallRequest, Usage

log = get_logger(__name__)


@dataclass(frozen=True)
class CompatPreset:
    base_url: str
    default_model: str
    requires_key: bool = True


OPENAI_COMPAT_PRESETS: dict[str, CompatPreset] = {
    "openai": CompatPreset("https://api.openai.com/v1", "gpt-5.1"),
    "openrouter": CompatPreset("https://openrouter.ai/api/v1", "anthropic/claude-sonnet-4.5"),
    "google": CompatPreset(
        "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.5-flash"
    ),
    "ollama": CompatPreset("http://localhost:11434/v1", "llama3.1", requires_key=False),
}


def _to_openai_messages(messages: list[Message], system: str | None) -> list[dict]:
    wire: list[dict] = []
    if system:
        wire.append({"role": "system", "content": system})
    for message in messages:
        if message.role == "system":
            wire.append({"role": "system", "content": message.content or ""})
        elif message.role == "assistant":
            entry: dict = {"role": "assistant", "content": message.content or None}
            if message.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments, default=str),
                        },
                    }
                    for call in message.tool_calls
                ]
            wire.append(entry)
        elif message.role == "tool":
            wire.append(
                {
                    "role": "tool",
                    "tool_call_id": message.tool_call_id,
                    "content": json.dumps(message.tool_result, default=str),
                }
            )
        else:
            wire.append({"role": "user", "content": message.content or ""})
    return wire


def _tool_defs(tools: list[ToolDef]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.schema,
            },
        }
        for tool in tools
    ]


def parse_response(payload: dict) -> LLMResponse:
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    tool_calls: list[ToolCallRequest] = []
    for raw in message.get("tool_calls") or []:
        function = raw.get("function") or {}
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            arguments = {"_malformed": function.get("arguments")}
        tool_calls.append(
            ToolCallRequest(id=raw.get("id") or f"call_{len(tool_calls)}",
                            name=function.get("name") or "", arguments=arguments)
        )
    finish = choice.get("finish_reason")
    if tool_calls or finish == "tool_calls":
        stop_reason = "tool_use"
    elif finish == "length":
        stop_reason = "max_tokens"
    else:
        stop_reason = "end"
    usage = payload.get("usage") or {}
    return LLMResponse(
        text=message.get("content") or None,
        tool_calls=tool_calls,
        usage=Usage(
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
        ),
        stop_reason=stop_reason,
    )


class OpenAICompatProvider(ChatProvider):
    def __init__(
        self,
        *,
        provider_name: str,
        base_url: str,
        api_key: str = "",
        model: str = "",
        timeout: float = 120.0,
    ) -> None:
        self.name = provider_name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        body: dict = {
            "model": self.model,
            "messages": _to_openai_messages(messages, system),
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = _tool_defs(tools)
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/chat/completions", json=body, headers=headers
                )
            except httpx.HTTPError as exc:
                raise LLMError(f"{self.name} request failed: {exc}") from exc
        if response.status_code != 200:
            raise LLMError(f"{self.name} returned {response.status_code}: {response.text[:300]}")
        return parse_response(response.json())
