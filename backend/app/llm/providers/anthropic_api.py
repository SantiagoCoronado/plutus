"""Anthropic Messages API over httpx — native tool use, true SSE streaming.

No SDK dependency: the wire format is stable and httpx is already a direct
dep. Retries once on 429/529 with the server-suggested delay.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import httpx

from app.core.logging import get_logger
from app.llm.base import ChatProvider, LLMError, tool_wire_format
from app.llm.tooldefs import ToolDef
from app.llm.types import (
    LLMResponse,
    Message,
    ProviderEvent,
    ResponseDone,
    TextDelta,
    ToolCallReady,
    ToolCallRequest,
    Usage,
)

log = get_logger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-5"
RETRYABLE_STATUS = (429, 529)


def _to_anthropic_messages(messages: list[Message]) -> list[dict]:
    wire: list[dict] = []
    for message in messages:
        if message.role == "system":
            continue  # system rides the top-level param
        if message.role == "assistant":
            blocks: list[dict] = []
            if message.content:
                blocks.append({"type": "text", "text": message.content})
            for call in message.tool_calls or []:
                blocks.append(
                    {"type": "tool_use", "id": call.id, "name": call.name,
                     "input": call.arguments}
                )
            wire.append({"role": "assistant", "content": blocks})
        elif message.role == "tool":
            wire.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": message.tool_call_id,
                            "content": json.dumps(message.tool_result, default=str),
                        }
                    ],
                }
            )
        else:
            wire.append({"role": "user", "content": message.content or ""})
    return wire


def _stop_reason(raw: str | None) -> str:
    if raw == "tool_use":
        return "tool_use"
    if raw == "max_tokens":
        return "max_tokens"
    return "end"


def parse_response(payload: dict) -> LLMResponse:
    text_parts: list[str] = []
    tool_calls: list[ToolCallRequest] = []
    for block in payload.get("content", []):
        if block.get("type") == "text":
            text_parts.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            tool_calls.append(
                ToolCallRequest(
                    id=block["id"], name=block["name"], arguments=block.get("input") or {}
                )
            )
    usage = payload.get("usage", {})
    return LLMResponse(
        text="".join(text_parts) or None,
        tool_calls=tool_calls,
        usage=Usage(
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
        ),
        stop_reason=_stop_reason(payload.get("stop_reason")),
    )


class AnthropicProvider(ChatProvider):
    name = "anthropic-api"

    def __init__(self, *, api_key: str, model: str = "", timeout: float = 120.0) -> None:
        self.api_key = api_key
        self.model = model or DEFAULT_MODEL
        self.timeout = timeout

    def _request_body(self, messages, tools, system, max_tokens, stream=False) -> dict:
        body: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": _to_anthropic_messages(messages),
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [tool_wire_format(tool) for tool in tools]
        if stream:
            body["stream"] = True
        return body

    def _headers(self) -> dict:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        body = self._request_body(messages, tools, system, max_tokens)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in (1, 2):
                try:
                    response = await client.post(API_URL, json=body, headers=self._headers())
                except httpx.HTTPError as exc:
                    raise LLMError(f"anthropic request failed: {exc}") from exc
                if response.status_code in RETRYABLE_STATUS and attempt == 1:
                    delay = float(response.headers.get("retry-after") or 2.0)
                    await asyncio.sleep(min(delay, 30.0))
                    continue
                if response.status_code != 200:
                    raise LLMError(
                        f"anthropic returned {response.status_code}: {response.text[:300]}"
                    )
                return parse_response(response.json())
        raise LLMError("anthropic: retries exhausted")  # pragma: no cover

    async def chat_stream(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> AsyncIterator[ProviderEvent]:
        body = self._request_body(messages, tools, system, max_tokens, stream=True)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST", API_URL, json=body, headers=self._headers()
            ) as response:
                if response.status_code != 200:
                    detail = (await response.aread()).decode()[:300]
                    raise LLMError(f"anthropic returned {response.status_code}: {detail}")
                async for event in self._parse_sse(response):
                    yield event

    async def _parse_sse(self, response) -> AsyncIterator[ProviderEvent]:
        text_parts: list[str] = []
        tool_calls: list[ToolCallRequest] = []
        blocks: dict[int, dict] = {}  # index -> {kind, id, name, json_parts}
        usage_in = 0
        usage_out = 0
        stop_reason = "end"

        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            try:
                payload = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            kind = payload.get("type")

            if kind == "message_start":
                usage_in = int(
                    payload.get("message", {}).get("usage", {}).get("input_tokens") or 0
                )
            elif kind == "content_block_start":
                index = payload["index"]
                block = payload.get("content_block", {})
                if block.get("type") == "tool_use":
                    blocks[index] = {
                        "kind": "tool_use", "id": block["id"],
                        "name": block["name"], "json_parts": [],
                    }
                else:
                    blocks[index] = {"kind": "text"}
            elif kind == "content_block_delta":
                index = payload["index"]
                delta = payload.get("delta", {})
                if delta.get("type") == "text_delta":
                    text = delta.get("text", "")
                    text_parts.append(text)
                    yield TextDelta(text)
                elif delta.get("type") == "input_json_delta":
                    blocks[index]["json_parts"].append(delta.get("partial_json", ""))
            elif kind == "content_block_stop":
                block = blocks.get(payload["index"])
                if block and block["kind"] == "tool_use":
                    raw = "".join(block["json_parts"]) or "{}"
                    try:
                        arguments = json.loads(raw)
                    except json.JSONDecodeError:
                        arguments = {"_malformed": raw}
                    call = ToolCallRequest(id=block["id"], name=block["name"],
                                           arguments=arguments)
                    tool_calls.append(call)
                    yield ToolCallReady(call)
            elif kind == "message_delta":
                stop_reason = _stop_reason(payload.get("delta", {}).get("stop_reason"))
                usage_out = int(payload.get("usage", {}).get("output_tokens") or usage_out)
            elif kind == "error":
                raise LLMError(f"anthropic stream error: {payload.get('error')}")

        yield ResponseDone(
            LLMResponse(
                text="".join(text_parts) or None,
                tool_calls=tool_calls,
                usage=Usage(input_tokens=usage_in, output_tokens=usage_out),
                stop_reason=stop_reason,
            )
        )
