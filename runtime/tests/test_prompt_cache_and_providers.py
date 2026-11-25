from __future__ import annotations

import asyncio
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import httpx2 as httpx

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import ModelResponse, ModelUsage, ScriptedModel
from eidos_runtime.model.config import MODEL_CATALOG, ModelConfigStore
from eidos_runtime.model.native_custom import OpenAIResponsesModelClient
from eidos_runtime.model.prompt_cache import apply_anthropic_cache_control, inject_anthropic_prompt_cache
from eidos_runtime.model.pydantic_factory import build_pydantic_model
from eidos_runtime.model.request_scope import foreground_request
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
        spec = MODEL_CATALOG.profile("gpt-5.2-codex")
        snapshot = spec.snapshot(MODEL_CATALOG.materialize("openai", "gpt-5.2-codex", "sk-test"))
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


def test_anthropic_prompt_cache_control_hook_and_payload() -> None:
    payload = {
        "model": "claude-sonnet-4-6",
        "messages": [
            {"role": "system", "content": "Follow instructions."},
            {"role": "user", "content": "Hello"},
        ],
        "tools": [
            {"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}
        ],
    }
    patched = apply_anthropic_cache_control(payload)
    assert patched["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert patched["messages"][1]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert patched["tools"][0]["cache_control"] == {"type": "ephemeral"}

    req = httpx.Request(
        "POST",
        "https://api.anthropic.com/v1/chat/completions",
        headers={"content-type": "application/json"},
        json=payload,
    )
    asyncio.run(inject_anthropic_prompt_cache(req))
    assert req.headers.get("anthropic-beta") == "prompt-caching-2024-07-31"
    decoded = json.loads(req.content.decode("utf-8"))
    assert decoded["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert decoded["tools"][0]["cache_control"] == {"type": "ephemeral"}


def test_builtin_overseas_providers_materialize_and_build_clients(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    store = ModelConfigStore(data)
    store.initialize()

    async def _exercise() -> None:
        for provider_id, model_id, expected_wire in (
            ("openai", "gpt-5.2-codex", "openai_responses"),
            ("anthropic", "claude-sonnet-4-6", "chat_completions"),
            ("gemini", "gemini-2.5-pro", "chat_completions"),
        ):
            cfg = store.create(provider_id=provider_id, model_id=model_id, api_key=f"key-{provider_id}")
            spec = MODEL_CATALOG.profile(cfg.id, config=cfg)
            assert spec.wire_api == expected_wire
            built = build_pydantic_model(cfg, wire_api=spec.wire_api)
            try:
                if provider_id == "anthropic":
                    assert inject_anthropic_prompt_cache in built.retry_client.http_client.event_hooks["request"]
            finally:
                await built.provider_client.close()
                await built.retry_client.aclose()

    asyncio.run(_exercise())


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
