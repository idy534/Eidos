from __future__ import annotations

import io
import json
from pathlib import Path
import threading

import anyio
import httpx2 as httpx
import pytest
from pydantic import ValidationError

from eidos_runtime.application.models import ModelApplication
from eidos_runtime.model.config import (
    CustomModelSettings, MODEL_CATALOG, ModelConfigError, ModelConfigStore,
    ModelReasoningConfig, normalize_model_base_url,
)
from eidos_runtime.model.discovery import CustomProviderRequest, CustomProviderResult, discover_custom_models
from eidos_runtime.protocol.methods import ModelCreateRequestDto, ModelOptionDto
from eidos_runtime.protocol.server import RuntimeServer


def settings(**changes) -> CustomModelSettings:
    return CustomModelSettings.model_validate({
        "providerName": "公司网关", "baseUrl": "http://127.0.0.1:3425/v1",
        "name": "网关模型", "contextWindowTokens": 32000, "maxOutputTokens": 4096,
        **changes,
    })


def store(tmp_path: Path) -> ModelConfigStore:
    result = ModelConfigStore(tmp_path)
    result.initialize()
    return result


def test_custom_configs_preserve_identity_secrets_limits_and_builtin_compatibility(tmp_path):
    configs = store(tmp_path)
    builtin = configs.create(provider_id="deepseek", model_id="deepseek-flash", api_key="original-secret")
    first = configs.create(provider_id="custom", model_id="deepseek-flash", api_key="", custom=settings())
    second = configs.create(provider_id="custom", model_id="deepseek-flash", api_key="custom-secret", custom=settings(baseUrl="https://gateway.invalid/v2"))
    assert len({builtin.id, first.id, second.id}) == 3
    reloaded = store(tmp_path)
    assert reloaded.get(first.id) == first
    public = reloaded.public_list()
    assert public[1].provider == "custom"
    assert public[1].context_window_tokens == 32000
    assert public[1].custom.base_url == "http://127.0.0.1:3425/v1"
    assert "original-secret" not in str(public) and "custom-secret" not in str(public)
    profile = MODEL_CATALOG.profile(first.id, config=first)
    assert profile.model_id == "deepseek-flash"
    assert profile.context_window_tokens == 32000
    assert profile.snapshot(first).model_id == first.id
    updated = configs.update(second.id, provider_id="custom", model_id="renamed-remote", api_key=None, custom=settings(baseUrl="https://gateway.invalid/v2", name="新名称"))
    assert updated.id == second.id and updated.model_id == "renamed-remote"
    assert updated.api_key == "custom-secret"
    with pytest.raises(ModelConfigError, match="重新填写"):
        configs.update(second.id, provider_id="custom", model_id="renamed-remote", api_key=None, custom=settings())
    cleared = configs.update(second.id, provider_id="custom", model_id="renamed-remote", api_key="", custom=settings())
    assert cleared.api_key == ""
    assert configs.delete(first.id).id == first.id
    assert configs.get(builtin.id).api_key == "original-secret"


def test_custom_reasoning_overrides_catalog_and_builtin_keys_stay_required(tmp_path):
    configs = store(tmp_path)
    with pytest.raises(ModelConfigError):
        configs.create(provider_id="deepseek", model_id="deepseek-flash", api_key="")
    reasoning = {"defaultSelection": "xhigh", "selections": ["none", "xhigh"]}
    custom = configs.create(provider_id="custom", model_id="deepseek-flash", api_key="", custom=settings(reasoning=reasoning, reasoningControl="configured"))
    assert MODEL_CATALOG.reasoning_selection(custom.id, None, config=custom) == "xhigh"
    assert MODEL_CATALOG.reasoning_selection(custom.id, "none", config=custom) == "none"
    configs.update(custom.id, provider_id="custom", model_id="deepseek-flash", api_key="", custom=settings())
    assert MODEL_CATALOG.reasoning_selection(custom.id, None, config=configs.get(custom.id)) is None


def test_custom_automatic_reasoning_preserves_supported_levels_after_restart(tmp_path):
    fixture = json.loads((Path(__file__).parents[2] / "protocol/fixtures/custom-models-v1.json").read_text())
    configs = store(tmp_path)
    created = configs.create(provider_id="custom", model_id="reasoner", api_key="", custom=settings(
        reasoningControl="configured", reasoning=fixture["automaticReasoning"],
    ))
    reloaded = store(tmp_path).get(created.id)
    assert reloaded.reasoning.default_selection is None
    assert reloaded.reasoning.selections == ("low", "high", "max")
    assert MODEL_CATALOG.reasoning_selection(created.id, None, config=reloaded) is None
    assert MODEL_CATALOG.reasoning_selection(created.id, "max", config=reloaded) == "max"
    with pytest.raises(ValueError, match="unsupported"):
        MODEL_CATALOG.reasoning_selection(created.id, "medium", config=reloaded)
    public = ModelOptionDto.model_validate(store(tmp_path).public_list()[0].model_dump(mode="json", by_alias=True))
    assert public.reasoning.default_selection is None
    assert public.custom.reasoning.selections == ("low", "high", "max")
    for invalid in ({"defaultSelection": "medium", "selections": ["high"]}, {"defaultSelection": None, "selections": []}):
        with pytest.raises(ValidationError):
            ModelReasoningConfig.model_validate(invalid)


def test_custom_provider_identity_survives_rename_and_restart_with_legacy_compatibility(tmp_path):
    configs = store(tmp_path)
    legacy = configs.create(provider_id="custom", model_id="model", api_key="", custom=settings())
    created = configs.create(provider_id="custom", model_id="model", api_key="", custom=settings(providerId="company-gateway"))
    updated = configs.update(created.id, provider_id="custom", model_id="model", api_key=None,
                             custom=settings(providerId="company-gateway", providerName="新名称"))
    assert updated.id == created.id
    reloaded = store(tmp_path)
    public = reloaded.public_list()
    assert public[0].id == legacy.id and public[0].custom.provider_id is None
    assert public[1].custom.provider_id == "company-gateway"
    assert public[1].custom.provider_name == "新名称"
    for invalid in ("", "with space", "../provider", "a" * 65):
        with pytest.raises(ValidationError):
            settings(providerId=invalid)


@pytest.mark.parametrize("url", ["file:///tmp/model", "https://user:secret@example.com/v1", "https://example.com/v1?key=secret", "https://example.com/#fragment", "http://localhost:bad/v1"])
def test_custom_urls_reject_non_http_credentials_and_ambiguous_paths(url):
    with pytest.raises(ValueError):
        normalize_model_base_url(url)


def test_normalization_and_custom_only_request_contract():
    assert normalize_model_base_url("http://localhost:3425/v1/chat/completions/") == "http://localhost:3425/v1"
    with pytest.raises(ValidationError):
        CustomProviderRequest(provider="deepseek", base_url="https://api.deepseek.com")
    with pytest.raises(ValidationError):
        settings(maxOutputTokens=64000)
    with pytest.raises(ValidationError):
        settings(reasoningFormat="minimax", reasoningControl="configured", reasoning={"defaultSelection": "high", "selections": ["high"]})


@pytest.mark.anyio
async def test_discovery_uses_only_models_get_and_never_inherits_credentials(monkeypatch):
    for name in ("OPENAI_API_KEY", "OPENAI_ADMIN_KEY", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID"):
        monkeypatch.setenv(name, "environment-secret")
    captured = []
    def handler(request):
        captured.append(request)
        return httpx.Response(200, json={"data": [
            {"id": "raw/model", "name": "Remote model", "context_window": 128000, "max_output_tokens": 8192,
             "input_modalities": ["text", "image"], "effort": {"supported_levels": ["low", "high"], "default_level": "high"}},
            {"id": "basic"}, {"id": "basic"},
            {"id": "router", "context_length": 64000, "top_provider": {"max_completion_tokens": 4096},
             "architecture": {"input_modalities": ["text"]}, "supported_parameters": ["tools"]},
        ]})
    result = await discover_custom_models("http://localhost:3425/v1", "", threading.Event(), transport=httpx.MockTransport(handler))
    assert result.state == "success", result
    assert len(captured) == 1 and captured[0].method == "GET"
    assert captured[0].url.path == "/v1/models"
    assert not captured[0].headers.get("Authorization")
    assert not captured[0].headers.get("OpenAI-Organization")
    assert "environment-secret" not in str(captured[0].headers)
    assert [model.id for model in result.models] == ["raw/model", "basic", "router"]
    first, basic, router = result.models
    assert first.supports_images is True and first.context_window_tokens == 128000
    assert first.reasoning == ModelReasoningConfig(default_selection="high", selections=("low", "high"))
    assert basic.context_window_tokens is None and basic.reasoning is None
    assert router.max_output_tokens == 4096 and router.supports_tool_call is True


@pytest.mark.anyio
@pytest.mark.parametrize(("status", "payload", "state"), [(401, {}, "error"), (404, {}, "unavailable"), (200, {"data": []}, "success"), (200, {"invalid": []}, "error"), (302, {}, "error")])
async def test_discovery_failures_empty_list_and_redirects_are_distinct(status, payload, state):
    captured = []
    def handler(request):
        captured.append(request)
        return httpx.Response(status, json=payload, headers={"location": "https://other.invalid/models"})
    result = await discover_custom_models("https://gateway.invalid/v1", "explicit-secret", threading.Event(), transport=httpx.MockTransport(handler))
    assert result.state == state
    assert len(captured) == 1 and captured[0].headers["authorization"] == "Bearer explicit-secret"
    assert "explicit-secret" not in result.model_dump_json()


@pytest.mark.anyio
async def test_discovery_is_bounded_and_cancellation_closes_request():
    too_large = await discover_custom_models("https://gateway.invalid", "", threading.Event(), transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b" " * (1024 * 1024 + 1))))
    assert too_large.state == "error"
    closed = threading.Event()
    async def blocked(_request):
        try:
            await anyio.sleep_forever()
        finally:
            closed.set()
    cancel = threading.Event()
    async def cancel_later():
        await anyio.sleep(0.02)
        cancel.set()
    async with anyio.create_task_group() as group:
        group.start_soon(cancel_later)
        result = await discover_custom_models("https://gateway.invalid", "", cancel, transport=httpx.MockTransport(blocked))
    assert result.state == "canceled" and closed.is_set()
    result = await discover_custom_models("https://gateway.invalid", "", threading.Event(), transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"data": [{"id": str(i)} for i in range(501)]})))
    assert len(result.models) == 500 and result.truncated


def test_stored_credentials_are_only_reused_for_the_same_custom_address(tmp_path):
    configs = store(tmp_path)
    model = configs.create(provider_id="custom", model_id="model", api_key="saved-secret", custom=settings())
    app = ModelApplication(configs)
    request = CustomProviderRequest(provider="custom", base_url=settings().base_url, config_id=model.id)
    assert app.discovery_credentials(request)[1] == "saved-secret"
    with pytest.raises(Exception, match="重新填写"):
        app.discovery_credentials(request.model_copy(update={"base_url": "https://different.invalid"}))
    assert app.discovery_credentials(request.model_copy(update={"api_key": ""}))[1] == ""


@pytest.mark.parametrize("automatic", [False, True])
def test_custom_model_rpc_roundtrip_and_fixture(tmp_path, automatic):
    fixture = json.loads((Path(__file__).parents[2] / "protocol/fixtures/custom-models-v1.json").read_text())
    ModelCreateRequestDto.model_validate(fixture["create"])
    CustomProviderRequest.model_validate(fixture["discover"])
    CustomProviderResult.model_validate(fixture["discovered"])
    output = io.StringIO()
    server = RuntimeServer(output, tmp_path / "data")
    server.store.initialize()
    server.model_config.initialize()
    server.initialized = True
    try:
        create = fixture["create"]
        if automatic:
            create = {**create, "custom": {**create["custom"], "reasoning": fixture["automaticReasoning"]}}
        server.handle({"jsonrpc": "2.0", "id": "client-custom-create", "method": "model/create", "params": create})
        response = json.loads(output.getvalue().splitlines()[-1])
        result = ModelOptionDto.model_validate(response["result"])
        assert result.provider == "custom" and result.model_id == "company/model"
        assert result.custom.max_output_tokens == 8192
        assert "apiKey" not in response["result"]
        from eidos_runtime.sandbox.sensitive import SensitiveScanner
        server.sensitive = SensitiveScanner()
        server.supervisor.prepare_next = lambda: None
        server._schedule_title_generation = lambda *_args: None
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        session = server.store.create_session(str(workspace))
        server.handle({"jsonrpc": "2.0", "id": "client-custom-run", "method": "run/start", "params": {
            "sessionId": session["id"], "modelId": result.id, "userInput": "fixture", **({} if automatic else {"reasoningSelection": "xhigh"}),
        }})
        run = next(json.loads(line)["result"] for line in output.getvalue().splitlines() if json.loads(line).get("id") == "client-custom-run")
        profile = server.store.read_model_profile(run["id"])
        assert profile.model_id == result.id and profile.remote_model_id == "company/model"
        assert profile.reasoning_selection == (None if automatic else "xhigh")
        assert profile.supports_reasoning and profile.max_output_tokens == 8192
        server.model_config.update(result.id, provider_id="custom", model_id="changed-model", api_key="", custom=settings())
        assert server._frozen_model_configs[run["id"]].model_id == "company/model"
        assert server._frozen_model_configs[run["id"]].custom.max_output_tokens == 8192
        server.store.cancel_run(run["id"])
        server.handle({"jsonrpc": "2.0", "id": "client-bad-discover", "method": "model/discoverCustom", "params": {"provider": "deepseek", "baseUrl": "https://api.deepseek.com"}})
        assert "error" in json.loads(output.getvalue().splitlines()[-1])
    finally:
        server.close()


@pytest.mark.parametrize(("protocol", "format_name", "selection", "expected", "declared_auto"), [
    ("chat_completions", "openai", "xhigh", {"reasoning_effort": "xhigh"}, False),
    ("chat_completions", "openai", "none", {"reasoning_effort": "none"}, False),
    ("chat_completions", "deepseek", "none", {"thinking": {"type": "disabled"}}, False),
    ("chat_completions", "deepseek", "high", {"thinking": {"type": "enabled"}, "reasoning_effort": "high"}, False),
    ("chat_completions", "minimax", "thinking", {"thinking": {"type": "adaptive"}}, False),
    ("chat_completions", "openai", None, {}, False),
    ("openai_responses", "openai", "high", {"reasoning": {"effort": "high"}}, False),
    ("chat_completions", "openai", None, {}, True),
    ("openai_responses", "openai", None, {}, True),
])
def test_custom_gateway_sends_remote_id_limits_and_real_reasoning_parameters(tmp_path, monkeypatch, protocol, format_name, selection, expected, declared_auto):
    from eidos_runtime.model import pydantic_factory
    from eidos_runtime.model.gateway import ModelGateway
    from eidos_runtime.model.retry_transport import build_retrying_http_client
    from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
    captured = []
    def handler(request):
        captured.append(request)
        if protocol == "openai_responses":
            events = [{"type": "response.completed", "sequence_number": 0, "response": {"id": "response", "object": "response", "created_at": 0, "status": "completed", "model": "raw/model", "output": [{"id": "message", "type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": "answer", "annotations": []}]}]}}]
            body = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
        else:
            chunks = [{"id": "response", "object": "chat.completion.chunk", "created": 0, "model": "raw/model", "choices": [{"index": 0, "delta": {"role": "assistant", "content": "answer"}, "finish_reason": None}]},
                      {"id": "response", "object": "chat.completion.chunk", "created": 0, "model": "raw/model", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}]
            body = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())
    monkeypatch.setattr(pydantic_factory, "build_retrying_http_client", lambda config, **kwargs: build_retrying_http_client(config, wrapped=httpx.MockTransport(handler), **kwargs))
    configs = store(tmp_path)
    model = configs.create(provider_id="custom", model_id="raw/model", api_key="", custom=settings(
        wireApi=protocol, reasoningFormat=format_name,
        reasoningControl="configured" if selection is not None or declared_auto else "provider_default",
        reasoning={"defaultSelection": selection, "selections": [selection] if selection is not None else ["low", "high", "max"]} if selection is not None or declared_auto else None,
    ))
    kernel = RuntimeAsyncKernel()
    kernel.start()
    try:
        lease = ModelGateway(configs, async_kernel=kernel).acquire_lease(model, reasoning_selection=selection)
        client = lease.client
        try:
            result = client.complete(({"type": "user", "content": "hello"},), threading.Event(), lambda _: None, instructions="fixture", allow_tools=False)
            assert result.text == "answer"
            assert client.profile_snapshot.model_id == model.id
            assert client.profile_snapshot.remote_model_id == "raw/model"
        finally:
            lease.close()
        assert len(captured) == 1
        request = captured[0]
        body = json.loads(request.content)
        assert body["model"] == "raw/model"
        assert not request.headers.get("authorization")
        assert request.url.path == "/v1/" + ("responses" if protocol == "openai_responses" else "chat/completions")
        assert body["max_output_tokens" if protocol == "openai_responses" else "max_tokens"] == 4096
        for key, value in expected.items():
            assert body[key] == value
        for key in {"thinking", "reasoning_effort", "reasoning"} - set(expected):
            assert key not in body
    finally:
        kernel.close()


def test_custom_xhigh_selection_is_accepted_by_every_saved_model_entry_point():
    from eidos_runtime.domain.agents import AgentModelSelection
    from eidos_runtime.domain.automation import AutomationModel
    from eidos_runtime.domain.channels import ChannelConfig
    agent = AgentModelSelection(provider_id="custom", config_id="custom-config", reasoning_selection="xhigh")
    automation = AutomationModel(model_id="custom-config", reasoning_selection="xhigh")
    channel = ChannelConfig(client_id="bot", model_id="custom-config", reasoning_selection="xhigh")
    assert all(model.reasoning_selection == "xhigh" for model in (agent, automation, channel))


@pytest.mark.anyio
async def test_discovery_preserves_declared_efforts_without_inventing_a_default():
    result = await discover_custom_models("https://gateway.invalid", "", threading.Event(), transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"data": [
        {"id": "reasoner", "effort": {"supported_levels": ["low", "high"]}},
    ]})))
    assert result.models[0].reasoning_selections == ["low", "high"]
    assert result.models[0].reasoning is None


@pytest.mark.anyio
@pytest.mark.parametrize(("levels", "expected"), [
    ([{"effort": "low"}, {"effort": "high"}, {"effort": "max"}, {"effort": "future"}, {"effort": "high"}, {"invalid": True}, None], ["low", "high", "max"]),
    (["low", "medium", "xhigh"], ["low", "medium", "xhigh"]),
    ({"invalid": True}, None),
])
async def test_discovery_reads_supported_reasoning_level_objects(levels, expected):
    result = await discover_custom_models("https://gateway.invalid", "", threading.Event(), transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"data": [
        {"id": "workbuddy-ai/deepseek-v4.1-flash", "display_name": "Deepseek-V4.1-Flash", "context_window": 1000000, "max_output_tokens": 128000,
         "reasoning": True, "supported_reasoning_levels": levels},
    ]})))
    model = result.models[0]
    assert model.name == "Deepseek-V4.1-Flash"
    assert model.context_window_tokens == 1000000 and model.max_output_tokens == 128000
    assert model.reasoning_selections == expected
    assert model.reasoning is None


@pytest.mark.anyio
async def test_discovery_keeps_names_and_reasoning_when_other_metadata_is_invalid():
    result = await discover_custom_models("https://gateway.invalid", "", threading.Event(), transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"data": [
        {"id": "reasoner", "display_name": "Company Reasoner", "context_window_tokens": 262144, "maxOutputTokens": 32768,
         "input_modalities": {"invalid": True}, "effort": {"supported_levels": ["low", "high", "future-level", "xhigh"], "default_level": "high"}},
        {"id": "canonical", "displayName": "Canonical Model", "contextWindowTokens": 64000, "max_output_tokens": "invalid",
         "reasoning": {"selections": ["low", "future-level", "max"], "defaultSelection": "max"}},
        {"id": "levels-only", "name": "Levels Only", "reasoningSelections": ["low", "high", "future-level", "xhigh"]},
        {"id": "unknown-default", "name": "Known Levels", "effort": {"supported_levels": ["low", "high"], "default_level": "future-level"}},
        {"id": 32, "name": "Invalid Identity"},
    ]})))
    first, canonical, levels_only, unknown_default = result.models
    assert first.name == "Company Reasoner" and first.supports_images is None
    assert first.context_window_tokens == 262144 and first.max_output_tokens == 32768
    assert first.reasoning == ModelReasoningConfig(default_selection="high", selections=("low", "high", "xhigh"))
    assert canonical.name == "Canonical Model" and canonical.context_window_tokens == 64000
    assert canonical.max_output_tokens is None and canonical.reasoning.default_selection == "max"
    assert levels_only.name == "Levels Only" and levels_only.reasoning_selections == ["low", "high", "xhigh"]
    assert levels_only.reasoning is None
    assert unknown_default.reasoning is None and unknown_default.reasoning_selections == ["low", "high"]


def test_custom_remote_ids_are_never_rewritten_by_builtin_aliases(tmp_path):
    configs = store(tmp_path)
    remote = "glm-5-2-260617"
    created = configs.create(provider_id="custom", model_id=remote, api_key="", custom=settings())
    updated = configs.update(created.id, provider_id="custom", model_id=remote, api_key=None, custom=settings(name="Edited"))
    assert updated.id == created.id and updated.model_id == remote


def test_custom_model_settings_defaults_when_tokens_omitted():
    from eidos_runtime.model.config import DEFAULT_CONTEXT_WINDOW_TOKENS, DEFAULT_MAX_OUTPUT_TOKENS
    s = CustomModelSettings.model_validate({
        "providerName": "公司网关",
        "baseUrl": "https://api.example.com/v1",
        "name": "缺省限制模型",
    })
    assert s.context_window_tokens == DEFAULT_CONTEXT_WINDOW_TOKENS
    assert s.max_output_tokens == DEFAULT_MAX_OUTPUT_TOKENS

