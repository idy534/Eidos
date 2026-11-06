from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from eidos_runtime.context.budget import estimate_model_request_budget
from eidos_runtime.infrastructure.qoder_model_driver import QoderModelDriver
from eidos_runtime.model.client import (
    CreditsUsage, FunctionToolDefinition, ModelRequestError, ModelUsage,
)
from eidos_runtime.model.config import MODEL_CATALOG, ModelConfigError, ModelConfigStore, QoderModelSettings
from eidos_runtime.model.qoder_client import QoderModelClient
from eidos_runtime.model.qoder_context import encode_qoder_context, qoder_budget_payload
from eidos_runtime.model.qoder_protocol import WorkerCall, WorkerEvent, WorkerRequest, WorkerResult
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel


@pytest.fixture
def config(tmp_path):
    store = ModelConfigStore(tmp_path / "data")
    store.initialize()
    return store, store.create(provider_id="qoder", model_id="efficient", api_key="", qoder=QoderModelSettings())


@pytest.fixture
def kernel():
    kernel = RuntimeAsyncKernel()
    kernel.start()
    yield kernel
    kernel.close()


def profile(config):
    return MODEL_CATALOG.profile(config.id, config=config).snapshot(config)


TOOL = FunctionToolDefinition(name="return_result", description="Return a value", parameters_json_schema={
    "type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"], "additionalProperties": False,
})


class Driver:
    def __init__(self, calls=(), finish="end_turn", text="answer"):
        self.calls, self.finish, self.text = calls, finish, text
        self.requests = []
        self.closed = False

    async def request(self, request, cancel, emit, timeout):
        self.requests.append(request)
        if self.text:
            emit(self.text)
        return WorkerEvent(request_id=request.request_id, payload=WorkerResult(
            calls=self.calls, finish_reason=self.finish, credits=0.125))

    async def close(self):
        self.closed = True


def test_qoder_config_roundtrip_has_no_http_credentials_or_fake_url(config):
    store, created = config
    assert store.get(created.id) == created
    assert created.url is None and created.api_key == ""
    public = store.public_list()[0]
    assert public.provider == "qoder" and public.qoder == QoderModelSettings()
    snapshot = profile(created)
    assert snapshot.wire_api == "qoder_sdk" and snapshot.remote_model_id == "efficient"
    assert snapshot.context_encoding == "text_v1" and snapshot.pydantic_ai_version is None
    assert snapshot.qoder_sdk_version == "1.0.17" and snapshot.qoder_resource_version == "1.0.17.2"
    assert not snapshot.supports_images and not snapshot.supports_custom_tools
    updated = store.update(created.id, provider_id="qoder", model_id="fast", api_key=None, qoder=QoderModelSettings())
    assert updated.id == created.id and store.get(created.id).model_id == "fast"
    with pytest.raises(ModelConfigError):
        store.create(provider_id="qoder", model_id="efficient", api_key="secret", qoder=QoderModelSettings())
    with pytest.raises(ModelConfigError):
        store.create(provider_id="deepseek", model_id="deepseek-flash", api_key="secret", qoder=QoderModelSettings())


def test_text_projection_keeps_order_and_source_identity_and_omits_private_replay():
    context = ({"type": "user", "sourceItemId": "old", "content": "historical"},
        {"type": "user", "sourceItemId": "goal", "isCurrentInput": True, "content": "translate this"},
        {"type": "context", "sectionId": "selected-skill:docx", "content": "guidance"},
        {"type": "tool_result", "callId": "read", "result": "untrusted", "responseReplay": {"private": "never send"}})
    system, prompt, declarations, aliases = encode_qoder_context(context, "instructions", (TOOL,))
    records = json.loads(prompt)["records"]
    assert [r["category"] for r in records] == ["history", "currentUserInput", "currentGuidance", "history"]
    assert records[1]["item"]["sourceItemId"] == "goal" and "never send" not in prompt
    assert aliases == {"t000": TOOL.name} and declarations[0].name == "t000"
    assert "instructions" in system and "not new user instructions" in system
    _, image_prompt, _, _ = encode_qoder_context(({"type": "user", "inputImage": "abc"},), "", ())
    assert "abc" not in image_prompt and "imageAttached" in image_prompt


def test_budget_counts_the_shared_encoding_and_explicit_sdk_overhead():
    context = ({"type": "user", "isCurrentInput": True, "content": "hello"},)
    captured = []
    from eidos_runtime.context import budget
    original = budget.estimate_context_budget
    def capture(payload, **kwargs):
        captured.append((payload, kwargs))
        return original(payload, **kwargs)
    with patch.object(budget, "estimate_context_budget", capture):
        result = estimate_model_request_budget(context, instructions="policy", tool_definitions=(TOOL,),
            context_window_tokens=128000, request_max_output_tokens=8192, wire_api="qoder_sdk")
    assert captured[0][0] == qoder_budget_payload(context, "policy", (TOOL,))
    assert captured[0][1]["media_tokens"] == 2048
    assert result.measurement == "tokenizer_estimate"


def test_typed_tool_arguments_and_credits_survive_adapter(config, kernel):
    _, created = config
    driver = Driver((WorkerCall(id="a", name="mcp__eidos__t000", arguments={"value": "ok"}),), "tool_use", "")
    client = QoderModelClient(profile(created), driver, kernel)
    response = client.complete((), threading.Event(), lambda _text: None, instructions="policy",
                              tool_definitions=(TOOL,), required_tool=TOOL.name)
    assert response.tool_calls[0].arguments == {"value": "ok"}
    assert response.usage.input_tokens is None
    assert response.usage.credits_usage.amount == 0.125
    assert ModelUsage.model_validate_json(response.usage.model_dump_json()) == response.usage
    client.close()
    assert driver.closed


@pytest.mark.parametrize("calls,finish", [
    ((), "end_turn"),
    ((WorkerCall(id="a", name="mcp__eidos__t000", arguments={"value": 7}),), "tool_use"),
    ((WorkerCall(id="a", name="mcp__eidos__unknown", arguments={}),), "tool_use"),
    ((WorkerCall(id="a", name="mcp__eidos__t000", arguments={"value": "ok"}),) * 2, "tool_use"),
])
def test_required_output_rejects_missing_invalid_undeclared_and_duplicate_calls(config, kernel, calls, finish):
    client = QoderModelClient(profile(config[1]), Driver(calls, finish, ""), kernel)
    with pytest.raises(ModelRequestError) as caught:
        client.complete((), threading.Event(), lambda _text: None, instructions="policy", tool_definitions=(TOOL,), required_tool=TOOL.name)
    assert caught.value.failure.code == "protocol_error"
    assert not caught.value.failure.retryable
    client.close()


def test_cancel_never_emits_late_text(config, kernel):
    cancel = threading.Event()
    class CancelingDriver(Driver):
        async def request(self, request, signal, emit, timeout):
            signal.set()
            emit("late response")
    client = QoderModelClient(profile(config[1]), CancelingDriver(), kernel)
    text = []
    with pytest.raises(ModelRequestError) as caught:
        client.complete((), cancel, text.append, instructions="")
    assert caught.value.failure.code == "sampling_canceled" and not text
    client.close()


@pytest.mark.parametrize("amount", [-1.0, float("inf"), float("nan")])
def test_credits_are_finite_and_nonnegative(amount):
    with pytest.raises(ValueError):
        CreditsUsage(amount=amount, source="request")


def test_worker_frame_limit_fails_before_process_start(tmp_path, kernel):
    driver = QoderModelDriver(SimpleNamespace(config_directory=tmp_path, command=["must-not-start"]))
    request = WorkerRequest(request_id="too-large", operation="sample", prompt="x" * (1024 * 1024))
    with pytest.raises(ModelRequestError) as caught:
        kernel.call(driver.request, request, threading.Event(), lambda _text: None, 1)
    assert caught.value.failure.code == "context_exceeded" and driver._process is None
    kernel.call(driver.close)


def test_shared_qoder_fixture_is_validated_by_the_python_contract():
    from pathlib import Path
    from eidos_runtime.protocol.methods import ModelCreateRequestDto
    from eidos_runtime.models.qoder import QoderConnectionRequest, QoderConnectionState
    fixture = json.loads((Path(__file__).resolve().parents[2] / "protocol/fixtures/qoder-model-v1.json").read_text())
    assert ModelCreateRequestDto.model_validate(fixture["create"]).qoder is not None
    assert not ModelCreateRequestDto.model_validate(fixture["textOnlyCreate"]).qoder.supports_tool_call
    assert QoderConnectionRequest.model_validate(fixture["connectionRequest"]).action == "refresh"
    assert QoderConnectionRequest.model_validate(fixture["checkRequest"]).action == "check"
    assert QoderConnectionState.model_validate_json(json.dumps(fixture["checkingState"])).phase == "checking"
    assert QoderConnectionState.model_validate_json(json.dumps(fixture["connectionState"])).models[0].id == "efficient"


@pytest.mark.integration
def test_eidos_loop_executes_tool_and_persists_qoder_usage_across_restart(config, kernel, tmp_path, monkeypatch):
    from eidos_runtime.db.storage import SessionStore
    from eidos_runtime.runtime.engine import RuntimeEngine
    monkeypatch.setenv("EIDOS_PROJECTLESS_ROOT", str(tmp_path / "projectless"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "fixture.txt").write_text("EIDOS_OUTER_TOOL_RESULT")
    snapshot = profile(config[1])
    class LoopDriver(Driver):
        async def request(self, request, cancel, emit, timeout):
            self.requests.append(request)
            if len(self.requests) == 1:
                alias = next(t.name for t in request.tools if t.description.startswith("Eidos tool: read_file\n"))
                calls = (WorkerCall(id="read-fixture", name="mcp__eidos__" + alias, arguments={"path": "fixture.txt"}),)
                return WorkerEvent(request_id=request.request_id, payload=WorkerResult(calls=calls, finish_reason="tool_use", credits=0.1))
            assert "EIDOS_OUTER_TOOL_RESULT" in request.prompt
            assert '"category":"currentUserInput"' in request.prompt
            emit("EIDOS_OUTER_TOOL_RESULT")
            return WorkerEvent(request_id=request.request_id, payload=WorkerResult(finish_reason="end_turn", credits=0.2))
    driver = LoopDriver()
    client = QoderModelClient(snapshot, driver, kernel)
    data = tmp_path / "runtime-data"
    store = SessionStore(data)
    store.initialize()
    try:
        session = store.create_session(str(workspace))
        run, _ = store.create_run(session["id"], "Read fixture.txt", model_id=config[1].id, model_profile=snapshot)
        RuntimeEngine(store, client, lambda _event: None).run(run["id"], threading.Event())
        assert store.read_run(run["id"])["status"] == "succeeded"
        assert store.connection.execute("SELECT count(*) FROM tool_calls WHERE status='completed'").fetchone()[0] == 1
        usages = [ModelUsage.model_validate_json(row[0]) for row in store.connection.execute("SELECT usage_json FROM model_attempts ORDER BY creation_seq")]
        assert [usage.credits_usage.amount for usage in usages] == [0.1, 0.2]
        assert all(usage.input_tokens is None for usage in usages)
    finally:
        client.close()
        store.close()
    restarted = SessionStore(data)
    restarted.initialize()
    try:
        assert restarted.read_model_profile(run["id"]) == snapshot
        assert restarted.read_run(run["id"])["status"] == "succeeded"
        assert restarted.connection.execute("SELECT count(*) FROM tool_calls").fetchone()[0] == 1
    finally:
        restarted.close()


def test_qoder_gateway_bypasses_http_factory_and_rejects_changed_frozen_profiles(config, kernel):
    from eidos_runtime.model.gateway import ModelGateway
    from eidos_runtime.model.qoder_protocol import RESOURCE_VERSION
    store, created = config
    frozen = profile(created)
    driver = Driver()
    with patch("eidos_runtime.model.gateway.build_pydantic_model", side_effect=AssertionError("HTTP factory must not run")), \
         patch("eidos_runtime.model.gateway.QoderWorkerBinding", return_value=SimpleNamespace(version=RESOURCE_VERSION)), \
         patch("eidos_runtime.model.gateway.QoderModelDriver", return_value=driver):
        gateway = ModelGateway(store, async_kernel=kernel, managed_resources=object())
        lease = gateway.acquire_lease(created, frozen_profile=frozen)
        assert lease.provider == "qoder" and lease.client.profile_snapshot == frozen
        lease.close()
        assert driver.closed
        changed = frozen.model_copy(update={"qoder_sdk_version": "different"})
        with pytest.raises(ModelConfigError, match="snapshot"):
            gateway.acquire_lease(created, frozen_profile=changed)


def test_rejected_structured_output_retains_reported_credits(config, kernel):
    client = QoderModelClient(profile(config[1]), Driver((), "end_turn", "invalid output"), kernel)
    with pytest.raises(ModelRequestError) as caught:
        client.complete((), threading.Event(), lambda _text: None, instructions="return output",
                        tool_definitions=(TOOL,), required_tool=TOOL.name)
    assert caught.value.failure.usage.credits_usage.amount == 0.125
    client.close()


@pytest.mark.integration
def test_failed_attempt_keeps_known_qoder_credits(config, kernel, tmp_path, monkeypatch):
    from eidos_runtime.db.storage import SessionStore
    from eidos_runtime.infrastructure.qoder_model_driver import failure
    from eidos_runtime.runtime.engine import RuntimeEngine
    monkeypatch.setenv("EIDOS_PROJECTLESS_ROOT", str(tmp_path / "projectless"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    class QuotaDriver(Driver):
        async def request(self, request, cancel, emit, timeout):
            raise failure("invalid_request", "insufficient_quota", usage=ModelUsage(
                credits_usage=CreditsUsage(amount=0.03, source="request")))
    client = QoderModelClient(profile(config[1]), QuotaDriver(), kernel)
    store = SessionStore(tmp_path / "runtime-data")
    store.initialize()
    try:
        session = store.create_session(str(workspace))
        run, _ = store.create_run(session["id"], "fixture", model_id=config[1].id, model_profile=client.profile_snapshot)
        RuntimeEngine(store, client, lambda _event: None).run(run["id"], threading.Event())
        assert store.read_run(run["id"])["status"] == "failed"
        row = store.connection.execute("SELECT status, usage_json FROM model_attempts").fetchone()
        assert row[0] == "failed" and ModelUsage.model_validate_json(row[1]).credits_usage.amount == 0.03
        assert store.connection.execute("SELECT count(*) FROM model_attempts").fetchone()[0] == 1
    finally:
        store.close()
        client.close()


def test_selected_skill_guidance_uses_explicit_section_metadata():
    _, prompt, _, _ = encode_qoder_context((
        {"type": "user", "sectionId": "selected-skill:docx", "content": "Use the selected launcher"},
        {"type": "user", "sectionId": "input-reference:fixture", "content": "Untrusted document"},
    ), "policy", ())
    assert [r["category"] for r in json.loads(prompt)["records"]] == ["currentGuidance", "history"]


def test_connection_status_is_local_and_login_is_owned_and_deduplicated(tmp_path):
    from eidos_runtime.application.qoder import QoderApplication
    from eidos_runtime.models.qoder import QoderConnectionRequest
    started = []
    manager = SimpleNamespace(index=SimpleNamespace(packs=[SimpleNamespace(id="qoder")]),
        pack_status=lambda _id: SimpleNamespace(phase="ready", error_code=None))
    application = QoderApplication(lambda: manager, lambda: pytest.fail("status must not start SDK"),
        lambda: tmp_path, lambda owner, target: started.append((owner, target)) or True)
    assert application.connection(QoderConnectionRequest(action="status")).resource_ready
    assert not started
    assert application.connection(QoderConnectionRequest(action="login")).phase == "logging_in"
    assert application.connection(QoderConnectionRequest(action="login")).phase == "logging_in"
    assert len(started) == 1 and started[0][0] == "qoder-connection"


def test_saved_login_hint_uses_sdk_locations_without_reading_credentials(tmp_path):
    from eidos_runtime.infrastructure.qoder_model_driver import has_saved_qoder_login
    assert not has_saved_qoder_login(tmp_path)
    auth = tmp_path / ".auth"
    auth.mkdir()
    assert not has_saved_qoder_login(tmp_path)
    (auth / "fixture").write_text("sdk-owned-fixture")
    with patch.object(type(tmp_path), "read_text", side_effect=AssertionError("must not read credentials")):
        assert has_saved_qoder_login(tmp_path)
    (auth / "fixture").unlink()
    oauth = tmp_path / "oauth_creds.json"
    oauth.write_text("sdk-owned-fixture")
    assert has_saved_qoder_login(tmp_path)
    oauth.unlink()
    oauth.symlink_to(auth)
    assert not has_saved_qoder_login(tmp_path)


def test_check_without_saved_login_does_not_start_sdk_or_browser(tmp_path):
    from eidos_runtime.application.qoder import QoderApplication
    from eidos_runtime.models.qoder import QoderConnectionRequest
    manager = SimpleNamespace(index=SimpleNamespace(packs=[SimpleNamespace(id="qoder")]),
        pack_status=lambda _id: SimpleNamespace(phase="ready", error_code=None))
    application = QoderApplication(lambda: manager, lambda: pytest.fail("must not start SDK"),
        lambda: tmp_path, lambda *_args: pytest.fail("must not start a task"))
    assert application.connection(QoderConnectionRequest(action="check")).phase == "idle"
    application = QoderApplication(lambda: None, lambda: pytest.fail("must not start SDK"),
        lambda: tmp_path, lambda *_args: pytest.fail("must not start a task"))
    result = application.connection(QoderConnectionRequest(action="check"))
    assert not result.resource_ready and result.error_code is None


@pytest.mark.parametrize("failure_code", [None, "authentication_failed", "provider_timeout", "sampling_canceled"])
def test_check_validates_saved_login_once_and_closes_owned_driver(tmp_path, kernel, failure_code):
    from eidos_runtime.application.qoder import QoderApplication
    from eidos_runtime.infrastructure.qoder_model_driver import failure
    from eidos_runtime.model.qoder_protocol import CatalogModel, WorkerCatalog
    from eidos_runtime.models.qoder import QoderConnectionRequest

    auth = tmp_path / "qoder/default/.auth"
    auth.mkdir(parents=True)
    (auth / "fixture").write_text("sdk-owned-fixture")
    tasks, operations = [], []
    manager = SimpleNamespace(index=SimpleNamespace(packs=[SimpleNamespace(id="qoder")]),
        pack_status=lambda _id: SimpleNamespace(phase="ready", error_code=None))
    driver = Driver()

    async def catalog(request, *_args):
        operations.append(request.operation)
        if failure_code:
            raise failure(failure_code, "fixture_failure")
        return WorkerEvent(request_id=request.request_id,
            payload=WorkerCatalog(models=(CatalogModel(id="fixture", name="Fixture", enabled=True),)))

    driver.request = catalog
    application = QoderApplication(lambda: manager, lambda: kernel, lambda: tmp_path,
        lambda _owner, task: tasks.append(task) or True)
    assert application.connection(QoderConnectionRequest(action="status")).phase == "idle"
    assert not tasks
    assert application.connection(QoderConnectionRequest(action="check")).phase == "checking"
    for action in ("status", "check", "refresh", "login"):
        assert application.connection(QoderConnectionRequest(action=action)).phase == "checking"
    assert len(tasks) == 1
    with patch("eidos_runtime.application.qoder.QoderWorkerBinding"), patch(
        "eidos_runtime.application.qoder.QoderModelDriver", return_value=driver
    ):
        tasks[0](threading.Event())
    state = application.connection(QoderConnectionRequest(action="status"))
    assert state.phase == ("error" if failure_code else "ready")
    assert state.error_code == failure_code
    assert operations == ["catalog"] and driver.closed
    assert len(tasks) == 1
    if failure_code is None:
        assert state.models[0].name == "Fixture"


def test_model_settings_freeze_catalog_limits_reasoning_and_image_capability(config):
    from eidos_runtime.model.config import ModelReasoningConfig
    store, _ = config
    settings = QoderModelSettings(context_window_tokens=1000000, max_output_tokens=32000,
        context_window_limit=1000000, output_token_limit=32000, supports_images=True,
        reasoning=ModelReasoningConfig(selections=("low", "high"), default_selection="high"))
    model = store.create(provider_id="qoder", model_id="ultimate", api_key="", qoder=settings)
    saved = store.get(model.id)
    assert saved.supports_images and saved.supports_reasoning
    assert saved.reasoning.default_selection == "high"
    assert MODEL_CATALOG.profile(saved.id, config=saved).snapshot(saved).supports_images
    with pytest.raises(ValueError, match="当前模型上限"):
        settings.model_copy().model_validate({**settings.model_dump(), "max_output_tokens": 32768})


def test_image_payload_and_reasoning_reach_worker_without_base64_in_text(config, kernel):
    driver = Driver()
    snapshot = profile(config[1]).model_copy(update={"supports_images": True, "reasoning_selection": "high"})
    client = QoderModelClient(snapshot, driver, kernel)
    client.complete(({"type":"user", "content":"Describe image", "inputImage":"aW1hZ2U=", "mime":"image/png"},),
                    threading.Event(), lambda _text: None, instructions="policy")
    request = driver.requests[0]
    assert request.images[0]["data"] == "aW1hZ2U=" and "aW1hZ2U=" not in request.prompt
    assert request.reasoning_effort == "high" and request.context_window_tokens == snapshot.context_window_tokens
    client.close()


def test_catalog_names_repair_saved_models_and_survive_edits_and_restart(config, kernel, tmp_path):
    from eidos_runtime.application.models import ModelApplication
    from eidos_runtime.application.qoder import QoderApplication
    from eidos_runtime.model.qoder_protocol import CatalogModel, WorkerCatalog
    from eidos_runtime.models.qoder import QoderConnectionRequest

    store, _ = config
    saved = store.create(provider_id="qoder", model_id="dfmodel", api_key="", qoder=QoderModelSettings())
    http = store.create(provider_id="deepseek", model_id="deepseek-flash", api_key="fixture-key")
    frozen = profile(saved)
    tasks = []
    manager = SimpleNamespace(index=SimpleNamespace(packs=[SimpleNamespace(id="qoder")]),
        pack_status=lambda _id: SimpleNamespace(phase="ready", error_code=None))
    driver = Driver()

    async def catalog(request, *_args):
        return WorkerEvent(request_id=request.request_id, payload=WorkerCatalog(models=(
            CatalogModel(id="dfmodel", name="DeepSeek-Flash", enabled=True),
            CatalogModel(id="kmodel_latest", name="Kimi-K3.1-Pro", enabled=True),
        )))

    driver.request = catalog
    application = QoderApplication(lambda: manager, lambda: kernel, lambda: tmp_path,
        lambda _owner, task: tasks.append(task) or True, on_catalog=store.refresh_qoder_names)
    assert application.model_name("dfmodel") is None
    assert application.connection(QoderConnectionRequest(action="refresh")).phase == "refreshing"
    with patch("eidos_runtime.application.qoder.QoderWorkerBinding"), patch(
        "eidos_runtime.application.qoder.QoderModelDriver", return_value=driver
    ):
        tasks[0](threading.Event())
    assert application.connection(QoderConnectionRequest(action="status")).phase == "ready"
    assert driver.closed
    assert application.model_name("dfmodel") == "DeepSeek-Flash"
    assert application.model_name("unknown") is None
    renamed = store.get(saved.id)
    assert renamed.name == "DeepSeek-Flash" and renamed.model_id == "dfmodel"
    assert profile(renamed) == frozen
    assert store.get(http.id) == http
    models = ModelApplication(store, qoder_name=application.model_name)
    created = models.create(provider="qoder", model_id="kmodel_latest", api_key="", qoder=QoderModelSettings())
    assert created["name"] == "Kimi-K3.1-Pro" and created["modelId"] == "kmodel_latest"
    updated = models.update(saved.id, provider="qoder", model_id="kmodel_latest", api_key=None, qoder=QoderModelSettings())
    assert updated["name"] == "Kimi-K3.1-Pro" and updated["id"] == saved.id

    restarted = ModelConfigStore(store.data_directory)
    restarted.initialize()
    offline = ModelApplication(restarted)
    edited = offline.update(saved.id, provider="qoder", model_id="kmodel_latest", api_key=None, qoder=QoderModelSettings(max_output_tokens=4096))
    assert edited["name"] == "Kimi-K3.1-Pro"
    offline.update(saved.id, provider="qoder", model_id="unknown", api_key=None, qoder=QoderModelSettings())
    assert restarted.get(saved.id).name == "unknown"


def test_catalog_name_refresh_does_not_lose_concurrent_model_changes(config):
    store, saved = config
    barrier = threading.Barrier(3)

    def refresh():
        barrier.wait()
        for _ in range(12):
            store.refresh_qoder_names({"efficient": "Efficient"})

    def create():
        barrier.wait()
        for index in range(12):
            store.create(provider_id="qoder", model_id=f"extra-{index}", api_key="", qoder=QoderModelSettings())

    def update():
        barrier.wait()
        for index in range(12):
            store.update(saved.id, provider_id="qoder", model_id="efficient", api_key=None,
                         qoder=QoderModelSettings(max_output_tokens=4096 + index))

    with ThreadPoolExecutor(max_workers=3) as executor:
        tasks = [executor.submit(task) for task in (refresh, create, update)]
        for task in tasks:
            task.result(timeout=10)
    assert len(store.list()) == 13
    assert store.get(saved.id).name == "Efficient"
    assert store.get(saved.id).qoder.max_output_tokens == 4107


def test_input_type_choices_persist_and_disable_tool_declarations(config, kernel):
    store, created = config
    assert "supportsToolCall" not in created.qoder.model_dump(by_alias=True)
    updated = store.update(created.id, provider_id="qoder", model_id="efficient", api_key=None,
        qoder=QoderModelSettings(supports_tool_call=False, supports_images=True))
    restarted = ModelConfigStore(store.data_directory)
    restarted.initialize()
    saved = restarted.get(updated.id)
    assert not saved.supports_tool_call and saved.supports_images
    assert saved.qoder.model_dump(by_alias=True)["supportsToolCall"] is False
    snapshot = profile(saved)
    assert not snapshot.supports_tools and snapshot.supports_images
    driver = Driver()
    client = QoderModelClient(snapshot, driver, kernel)
    client.complete((), threading.Event(), lambda _text: None, instructions="policy", tool_definitions=(TOOL,))
    assert not driver.requests[0].tools
    with pytest.raises(ValueError, match="model_output_tool_invalid"):
        client.complete((), threading.Event(), lambda _text: None, instructions="policy", tool_definitions=(TOOL,), required_tool=TOOL.name)
    assert len(driver.requests) == 1
    client.close()
