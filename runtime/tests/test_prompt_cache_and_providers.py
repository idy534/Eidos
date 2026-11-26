from __future__ import annotations

import asyncio
import json
from pathlib import Path
import threading
from types import SimpleNamespace

from anthropic import APIStatusError as AnthropicAPIStatusError, AsyncAnthropic
import httpx2 as httpx
from pydantic_ai.messages import ModelResponse as PAIModelResponse, ThinkingPart, UserPromptPart
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider
import pytest

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import FunctionToolDefinition, ModelResponse, ModelUsage, ScriptedModel
from eidos_runtime.model.config import MODEL_CATALOG, ModelConfigError, ModelConfigStore
from eidos_runtime.model.native_custom import OpenAIResponsesModelClient
from eidos_runtime.model.pydantic_ai_client import PydanticAIModelClient, encode_context, map_model_error
from eidos_runtime.model.pydantic_factory import build_pydantic_model
from eidos_runtime.model.replay import ModelReasoningPart, ModelResponseReplay
from eidos_runtime.model.request_scope import foreground_request
from eidos_runtime.model.retry import is_retryable_http_status
from eidos_runtime.persistence.context_management import ContextManagementRepository
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
from eidos_runtime.runtime.engine import RuntimeEngine


def test_openai_responses_receives_session_prompt_cache_key() -> None:
    captured_kwargs: dict[str, object] = {}

    class _FakeResponses:
        async def create(self, **kwargs: object) -> object:
            captured_kwargs.update(kwargs)
            return SimpleNamespace(
                id="resp-1",
                status="completed",
                output=[
                    SimpleNamespace(
                        type="message",
                        id="msg-1",
                        content=[SimpleNamespace(type="output_text", text="ok")],
                    )
                ],
                usage=SimpleNamespace(
                    input_tokens=100,
                    output_tokens=10,
                    total_tokens=110,
                    input_tokens_details=SimpleNamespace(cached_tokens=80),
                ),
            )

    kernel = RuntimeAsyncKernel()
    kernel.start()
    try:
        spec = MODEL_CATALOG.profile("gpt-6.2-astra")
        snapshot = spec.snapshot(MODEL_CATALOG.materialize("openai", "gpt-6.2-astra", "sk-test"))
        client = OpenAIResponsesModelClient(
            spec,
            openai_client=SimpleNamespace(responses=_FakeResponses()),
            retry_transport=None,
            profile_snapshot=snapshot,
            async_kernel=kernel,
        )
        with foreground_request(None, prompt_cache_key="eidos:session-cache-1"):
            response = client.complete(
                ({"type": "user", "content": "hi"},),
                threading.Event(),
                lambda _delta: None,
                instructions="You are helpful",
                allow_tools=False,
            )
        assert captured_kwargs.get("prompt_cache_key") == "eidos:session-cache-1"
        assert response.usage.cache_read_tokens == 80
    finally:
        kernel.close()


def _sse_event(name: str, payload: dict[str, object]) -> str:
    return f"event: {name}\ndata: {json.dumps(payload)}\n\n"


def _anthropic_stream_body() -> bytes:
    return "".join([
        _sse_event("message_start", {
            "type": "message_start",
            "message": {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5-5",
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 120,
                    "cache_creation_input_tokens": 40,
                    "cache_read_input_tokens": 80,
                    "output_tokens": 1,
                },
            },
        }),
        _sse_event("content_block_start", {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": "", "signature": ""},
        }),
        _sse_event("content_block_delta", {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "Step 1"},
        }),
        _sse_event("content_block_delta", {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "signature_delta", "signature": "sig-123"},
        }),
        _sse_event("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _sse_event("content_block_start", {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "text", "text": ""},
        }),
        _sse_event("content_block_delta", {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "text_delta", "text": "Done."},
        }),
        _sse_event("content_block_stop", {"type": "content_block_stop", "index": 1}),
        _sse_event("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 15},
        }),
        _sse_event("message_stop", {"type": "message_stop"}),
    ]).encode("utf-8")


def test_anthropic_native_messages_request_streaming_usage_and_replay() -> None:
    captured_requests: list[tuple[str, dict[str, object]]] = []
    sse_body = _anthropic_stream_body()

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append((str(request.url), json.loads(request.content.decode("utf-8"))))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse_body)

    kernel = RuntimeAsyncKernel()
    kernel.start()
    try:
        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        anthropic_client = AsyncAnthropic(
            api_key="sk-ant-test",
            base_url="https://api.anthropic.com",
            max_retries=0,
            http_client=http_client,
        )
        model = AnthropicModel("claude-opus-5-5", provider=AnthropicProvider(anthropic_client=anthropic_client))
        spec = MODEL_CATALOG.profile("claude-opus-5-5")
        cfg = MODEL_CATALOG.materialize("anthropic", "claude-opus-5-5", "sk-ant-test")
        client = PydanticAIModelClient(
            model,
            spec,
            provider_client=anthropic_client,
            profile_snapshot=spec.snapshot(cfg, reasoning_selection="xhigh"),
            reasoning_selection="xhigh",
            async_kernel=kernel,
        )
        try:
            chunks: list[str] = []
            response = client.complete(
                ({"type": "user", "content": "Hello"},),
                threading.Event(),
                chunks.append,
                instructions="Follow instructions.",
                allow_tools=True,
                tool_definitions=(FunctionToolDefinition(
                    name="read_file",
                    description="Read file",
                    parameters_json_schema={"type": "object"},
                ),),
            )
        finally:
            client.close()

        assert len(captured_requests) == 1
        url, body = captured_requests[0]
        assert url.split("?", 1)[0] == "https://api.anthropic.com/v1/messages"
        assert body["model"] == "claude-opus-5-5"
        assert body["thinking"] == {"type": "adaptive"}
        assert body["output_config"] == {"effort": "xhigh"}
        assert body["cache_control"] == {"type": "ephemeral", "ttl": "5m"}
        assert body["system"][-1]["cache_control"] == {"type": "ephemeral", "ttl": "5m"}
        assert body["tools"][-1]["cache_control"] == {"type": "ephemeral", "ttl": "5m"}

        assert "".join(chunks) == "Done."
        assert response.text == "Done."
        assert response.wire_api == "anthropic_messages"
        assert response.provider_name == "anthropic"
        assert len(response.reasoning_parts) == 1
        assert response.reasoning_parts[0].content == "Step 1"
        assert response.reasoning_parts[0].signature == "sig-123"
        assert response.usage is not None
        assert response.usage.cache_read_tokens == 80
        assert response.usage.cache_write_tokens == 40
    finally:
        kernel.close()

    replay = ModelResponseReplay(
        wire_api="anthropic_messages",
        provider_name="anthropic",
        tool_call_ids=("call-1",),
        reasoning_parts=(
            ModelReasoningPart(content="Step 1", signature="sig-123", provider_name="anthropic"),
        ),
    )
    context = (
        {
            "type": "assistant",
            "content": "",
            "responseId": "resp-1",
            "responseReplay": replay.model_dump(mode="json"),
        },
        {
            "type": "tool_call",
            "name": "read_file",
            "callId": "call-1",
            "arguments": "{}",
            "responseId": "resp-1",
        },
        {
            "type": "tool_result",
            "name": "read_file",
            "callId": "call-1",
            "result": "ok",
        },
    )
    native_messages = encode_context(context, reasoning_required=True, wire_api="anthropic_messages")
    assert isinstance(native_messages[0], PAIModelResponse)
    assert isinstance(native_messages[0].parts[0], ThinkingPart)
    assert native_messages[0].parts[0].signature == "sig-123"

    fallback_messages = encode_context(context, reasoning_required=True, wire_api="chat_completions")
    assert isinstance(fallback_messages[0].parts[0], UserPromptPart)
    assert "Historical model response" in str(fallback_messages[0].parts[0].content)


def test_anthropic_wire_does_not_force_tool_choice_for_required_tool() -> None:
    """Claude 5.x models return 400 for a forced tool_choice; the summary path names the tool instead."""
    captured: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_anthropic_stream_body())

    kernel = RuntimeAsyncKernel()
    kernel.start()
    try:
        anthropic_client = AsyncAnthropic(
            api_key="sk-ant-test",
            base_url="https://api.anthropic.com",
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        model = AnthropicModel("claude-sonnet-5-5", provider=AnthropicProvider(anthropic_client=anthropic_client))
        spec = MODEL_CATALOG.profile("claude-sonnet-5-5")
        cfg = MODEL_CATALOG.materialize("anthropic", "claude-sonnet-5-5", "sk-ant-test")
        client = PydanticAIModelClient(
            model,
            spec,
            provider_client=anthropic_client,
            profile_snapshot=spec.snapshot(cfg),
            async_kernel=kernel,
        )
        try:
            client.complete(
                ({"type": "user", "content": "Summarize"},),
                threading.Event(),
                lambda _delta: None,
                instructions="Return submit_context_summary once.",
                allow_tools=True,
                tool_definitions=(FunctionToolDefinition(
                    name="submit_context_summary",
                    description="Submit the summary",
                    parameters_json_schema={"type": "object"},
                ),),
                required_tool="submit_context_summary",
            )
        finally:
            client.close()
    finally:
        kernel.close()

    assert len(captured) == 1
    tool_choice = captured[0].get("tool_choice")
    assert tool_choice is None or tool_choice.get("type") == "auto"
    assert [tool["name"] for tool in captured[0]["tools"]] == ["submit_context_summary"]


def test_builtin_overseas_providers_materialize_and_build_clients(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    store = ModelConfigStore(data)
    store.initialize()

    async def _exercise() -> None:
        for provider_id, model_id, expected_wire in (
            ("openai", "gpt-6.2-sol", "openai_responses"),
            ("anthropic", "claude-opus-5-5", "anthropic_messages"),
            ("gemini", "gemini-4-neon", "chat_completions"),
        ):
            cfg = store.create(provider_id=provider_id, model_id=model_id, api_key=f"key-{provider_id}")
            spec = MODEL_CATALOG.profile(cfg.id, config=cfg)
            assert spec.wire_api == expected_wire
            built = build_pydantic_model(cfg, wire_api=spec.wire_api)
            try:
                if provider_id == "anthropic":
                    assert isinstance(built.model, AnthropicModel)
                    assert isinstance(built.provider_client, AsyncAnthropic)
                    assert str(built.provider_client.base_url).rstrip("/") == "https://api.anthropic.com"
            finally:
                await built.provider_client.close()
                await built.retry_client.aclose()

    asyncio.run(_exercise())


def test_builtin_overseas_models_build_expected_clients_and_reject_legacy() -> None:
    expected_models = (
        ("openai", "gpt-6.2-astra", "openai_responses"),
        ("openai", "gpt-6.2-sol", "openai_responses"),
        ("openai", "gpt-6-luna", "openai_responses"),
        ("anthropic", "claude-fable-5-2", "anthropic_messages"),
        ("anthropic", "claude-opus-5-5", "anthropic_messages"),
        ("anthropic", "claude-sonnet-5-5", "anthropic_messages"),
        ("anthropic", "claude-haiku-5-5", "anthropic_messages"),
        ("gemini", "gemini-4-carbon", "chat_completions"),
        ("gemini", "gemini-4-neon", "chat_completions"),
        ("gemini", "gemini-4-hydrogen", "chat_completions"),
    )

    async def _exercise() -> None:
        for provider_id, model_id, expected_wire in expected_models:
            cfg = MODEL_CATALOG.materialize(provider_id, model_id, f"key-{provider_id}")
            spec = MODEL_CATALOG.profile(cfg.id, config=cfg)
            assert spec.wire_api == expected_wire
            built = build_pydantic_model(cfg, wire_api=spec.wire_api)
            try:
                if provider_id == "anthropic":
                    assert isinstance(built.model, AnthropicModel)
                    assert isinstance(built.provider_client, AsyncAnthropic)
                    assert str(built.provider_client.base_url).rstrip("/") == "https://api.anthropic.com"
            finally:
                await built.provider_client.close()
                await built.retry_client.aclose()

    asyncio.run(_exercise())

    for legacy_provider, legacy_model in (
        ("openai", "gpt-5.2-codex"),
        ("openai", "gpt-5.2"),
        ("anthropic", "claude-sonnet-4-6"),
        ("anthropic", "claude-opus-4-6"),
        ("gemini", "gemini-2.5-pro"),
        ("gemini", "gemini-2.5-flash"),
    ):
        with pytest.raises(ModelConfigError, match="unsupported"):
            MODEL_CATALOG.lookup(legacy_provider, legacy_model)


def test_gemini_xhigh_and_anthropic_error_mapping() -> None:
    kernel = RuntimeAsyncKernel()
    kernel.start()
    try:
        spec = MODEL_CATALOG.profile("gemini-4-neon")
        cfg = MODEL_CATALOG.materialize("gemini", "gemini-4-neon", "key-gemini")
        built = build_pydantic_model(cfg, wire_api=spec.wire_api)
        client = PydanticAIModelClient(
            built.model,
            spec,
            openai_client=built.provider_client,
            provider_client=built.provider_client,
            retry_transport=built.retry_client,
            reasoning_selection="xhigh",
            async_kernel=kernel,
        )
        try:
            assert client._reasoning_effort == "xhigh"
        finally:
            client.close()
    finally:
        kernel.close()

    assert is_retryable_http_status(529) is True

    overloaded_req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    overloaded_resp = httpx.Response(529, request=overloaded_req, json={"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}})
    overloaded_err = AnthropicAPIStatusError("Overloaded", response=overloaded_resp, body=overloaded_resp.json())
    mapped_overloaded = map_model_error(overloaded_err)
    assert mapped_overloaded.code == "provider_unavailable"
    assert mapped_overloaded.retryable is True
    assert mapped_overloaded.status_code == 529

    too_long_resp = httpx.Response(400, request=overloaded_req, json={"type": "error", "error": {"type": "invalid_request_error", "message": "prompt is too long: 1050000 tokens > 1000000 maximum"}})
    too_long_err = AnthropicAPIStatusError("Bad request", response=too_long_resp, body=too_long_resp.json())
    mapped_too_long = map_model_error(too_long_err)
    assert mapped_too_long.code == "context_exceeded"
    assert mapped_too_long.retryable is False


def test_context_details_exposes_cache_read_tokens_and_hit_rate(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        session = store.create_session(str(workspace))
        run, _ = store.create_run(session["id"], "Check cache")
        model = ScriptedModel([
            ModelResponse(
                text="ok",
                usage=ModelUsage(input_tokens=1_000, output_tokens=50, cache_read_tokens=750),
            )
        ])
        RuntimeEngine(store, model, lambda _message: None).run(run["id"], threading.Event())
        details = ContextManagementRepository(store.database).details(
            run["id"], store.read_model_profile(run["id"])
        )
        assert details.cache_read_tokens == 750
        assert details.cache_hit_rate_percent == 75.0
    finally:
        store.close()
