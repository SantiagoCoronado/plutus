"""Provider normalizers: canned Anthropic/OpenAI payloads (and an Anthropic SSE
stream) must come out as identical provider-neutral shapes."""

import json

import httpx
import pytest
import respx

from app.llm.base import LLMError, ProviderUnavailableError
from app.llm.providers.anthropic_api import (
    AnthropicProvider,
    _to_anthropic_messages,
)
from app.llm.providers.claude_sidecar import ClaudeSidecarProvider
from app.llm.providers.fake import FakeProvider
from app.llm.providers.openai_compat import (
    OPENAI_COMPAT_PRESETS,
    OpenAICompatProvider,
    _to_openai_messages,
)
from app.llm.tooldefs import TOOLS
from app.llm.types import (
    LLMResponse,
    Message,
    ResponseDone,
    TextDelta,
    ToolCallReady,
    ToolCallRequest,
    Usage,
)

TOOL = TOOLS["get_news"]


@pytest.fixture
def anyio_backend():
    return "asyncio"


pytestmark = pytest.mark.anyio

CONVERSATION = [
    Message(role="user", content="how is AAPL?"),
    Message(
        role="assistant",
        content="Let me check.",
        tool_calls=[ToolCallRequest(id="tc_1", name="get_news", arguments={"symbol": "AAPL"})],
    ),
    Message(role="tool", tool_call_id="tc_1", tool_name="get_news",
            tool_result={"status": "ok", "result": {"headlines": []}}),
]


class TestMessageConversion:
    def test_anthropic_wire_shape(self):
        wire = _to_anthropic_messages(CONVERSATION)
        assert wire[0] == {"role": "user", "content": "how is AAPL?"}
        assert wire[1]["content"][0] == {"type": "text", "text": "Let me check."}
        assert wire[1]["content"][1]["type"] == "tool_use"
        assert wire[1]["content"][1]["input"] == {"symbol": "AAPL"}
        assert wire[2]["role"] == "user"
        assert wire[2]["content"][0]["type"] == "tool_result"
        assert wire[2]["content"][0]["tool_use_id"] == "tc_1"

    def test_openai_wire_shape(self):
        wire = _to_openai_messages(CONVERSATION, system="be brief")
        assert wire[0] == {"role": "system", "content": "be brief"}
        assert wire[2]["tool_calls"][0]["function"]["name"] == "get_news"
        assert json.loads(wire[2]["tool_calls"][0]["function"]["arguments"]) == {
            "symbol": "AAPL"
        }
        assert wire[3]["role"] == "tool" and wire[3]["tool_call_id"] == "tc_1"


class TestAnthropicProvider:
    @respx.mock
    @pytest.mark.anyio
    async def test_chat_parses_tool_use(self):
        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(200, json={
                "content": [
                    {"type": "text", "text": "Checking news."},
                    {"type": "tool_use", "id": "tu_1", "name": "get_news",
                     "input": {"symbol": "AAPL"}},
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 120, "output_tokens": 30},
            })
        )
        provider = AnthropicProvider(api_key="sk-test")
        response = await provider.chat([Message(role="user", content="news?")], tools=[TOOL])
        assert response.text == "Checking news."
        assert response.tool_calls[0].name == "get_news"
        assert response.stop_reason == "tool_use"
        assert response.usage.total == 150

    @respx.mock
    @pytest.mark.anyio
    async def test_error_status_raises_llm_error(self):
        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(401, json={"error": {"message": "bad key"}})
        )
        provider = AnthropicProvider(api_key="sk-bad")
        with pytest.raises(LLMError, match="401"):
            await provider.chat([Message(role="user", content="hi")])

    @respx.mock
    @pytest.mark.anyio
    async def test_stream_normalizes_events(self):
        sse = "".join(
            f"data: {json.dumps(payload)}\n\n"
            for payload in [
                {"type": "message_start", "message": {"usage": {"input_tokens": 100}}},
                {"type": "content_block_start", "index": 0,
                 "content_block": {"type": "text"}},
                {"type": "content_block_delta", "index": 0,
                 "delta": {"type": "text_delta", "text": "Hello "}},
                {"type": "content_block_delta", "index": 0,
                 "delta": {"type": "text_delta", "text": "world"}},
                {"type": "content_block_stop", "index": 0},
                {"type": "content_block_start", "index": 1,
                 "content_block": {"type": "tool_use", "id": "tu_9", "name": "get_news"}},
                {"type": "content_block_delta", "index": 1,
                 "delta": {"type": "input_json_delta", "partial_json": '{"symb'}},
                {"type": "content_block_delta", "index": 1,
                 "delta": {"type": "input_json_delta", "partial_json": 'ol": "AAPL"}'}},
                {"type": "content_block_stop", "index": 1},
                {"type": "message_delta", "delta": {"stop_reason": "tool_use"},
                 "usage": {"output_tokens": 25}},
                {"type": "message_stop"},
            ]
        )
        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(200, text=sse,
                                        headers={"content-type": "text/event-stream"})
        )
        provider = AnthropicProvider(api_key="sk-test")
        events = [
            event
            async for event in provider.chat_stream(
                [Message(role="user", content="news?")], tools=[TOOL]
            )
        ]
        deltas = [e.text for e in events if isinstance(e, TextDelta)]
        assert deltas == ["Hello ", "world"]
        calls = [e.call for e in events if isinstance(e, ToolCallReady)]
        assert calls == [ToolCallRequest(id="tu_9", name="get_news",
                                         arguments={"symbol": "AAPL"})]
        done = [e for e in events if isinstance(e, ResponseDone)][0]
        assert done.response.text == "Hello world"
        assert done.response.usage == Usage(input_tokens=100, output_tokens=25)
        assert done.response.stop_reason == "tool_use"


class TestOpenAICompatProvider:
    @respx.mock
    @pytest.mark.anyio
    async def test_chat_parses_tool_calls(self):
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={
                "choices": [{
                    "message": {
                        "content": None,
                        "tool_calls": [{
                            "id": "call_1", "type": "function",
                            "function": {"name": "get_news",
                                         "arguments": '{"symbol": "AAPL"}'},
                        }],
                    },
                    "finish_reason": "tool_calls",
                }],
                "usage": {"prompt_tokens": 80, "completion_tokens": 12},
            })
        )
        provider = OpenAICompatProvider(
            provider_name="openai", base_url="https://api.openai.com/v1",
            api_key="sk-x", model="gpt-5.1",
        )
        response = await provider.chat([Message(role="user", content="news?")], tools=[TOOL])
        assert response.tool_calls[0].arguments == {"symbol": "AAPL"}
        assert response.stop_reason == "tool_use"
        assert response.usage.input_tokens == 80

    @respx.mock
    @pytest.mark.anyio
    async def test_default_stream_wraps_chat(self):
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={
                "choices": [{"message": {"content": "All quiet."},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
            })
        )
        provider = OpenAICompatProvider(
            provider_name="openai", base_url="https://api.openai.com/v1",
            api_key="sk-x", model="gpt-5.1",
        )
        events = [
            event
            async for event in provider.chat_stream([Message(role="user", content="hi")])
        ]
        assert isinstance(events[0], TextDelta) and events[0].text == "All quiet."
        assert isinstance(events[-1], ResponseDone)

    def test_presets_cover_all_compat_providers(self):
        assert set(OPENAI_COMPAT_PRESETS) == {"openai", "openrouter", "google", "ollama"}
        assert not OPENAI_COMPAT_PRESETS["ollama"].requires_key


class TestSidecarDriver:
    @respx.mock
    @pytest.mark.anyio
    async def test_stream_normalizes_agent_events(self):
        sse = (
            'event: text_delta\ndata: {"text": "Looking"}\n\n'
            'event: tool_call\ndata: {"tool_call_id": "t1", "name": "get_news", '
            '"arguments": {"symbol": "AAPL"}}\n\n'
            'event: tool_result\ndata: {"tool_call_id": "t1", "name": "get_news", '
            '"ok": true, "summary": "3 headlines"}\n\n'
            'event: done\ndata: {"session_id": "sdk-abc", '
            '"usage": {"input_tokens": 900, "output_tokens": 210}}\n\n'
        )
        respx.post("http://sidecar.test/chat/stream").mock(
            return_value=httpx.Response(200, text=sse,
                                        headers={"content-type": "text/event-stream"})
        )
        provider = ClaudeSidecarProvider(base_url="http://sidecar.test")
        events = [
            event
            async for event in provider.run_loop(
                system="s", user_message="news?", tools=[TOOL],
                conversation_id=7, session_id=None, max_turns=15,
            )
        ]
        assert [e.type for e in events] == ["text_delta", "tool_call", "tool_result", "done"]
        assert events[3].data["session_id"] == "sdk-abc"

    @pytest.mark.anyio
    async def test_unreachable_is_provider_unavailable(self):
        provider = ClaudeSidecarProvider(base_url="http://127.0.0.1:1")
        with pytest.raises(ProviderUnavailableError, match="not reachable"):
            async for _ in provider.run_loop(
                system="s", user_message="x", tools=[], conversation_id=1,
                session_id=None, max_turns=1,
            ):
                pass


class TestFakeProvider:
    @pytest.mark.anyio
    async def test_scripted_responses_pop_in_order(self):
        fake = FakeProvider(script=[
            LLMResponse(text=None, tool_calls=[
                ToolCallRequest(id="1", name="get_news", arguments={"symbol": "AAPL"})
            ], stop_reason="tool_use"),
            LLMResponse(text="done", usage=Usage(10, 5)),
        ])
        first = await fake.chat([Message(role="user", content="go")], tools=[TOOL])
        assert first.tool_calls[0].name == "get_news"
        second = await fake.chat([Message(role="user", content="go")])
        assert second.text == "done"
        assert fake.calls[0]["tools"] == ["get_news"]
