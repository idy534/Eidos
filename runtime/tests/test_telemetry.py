from __future__ import annotations

from pathlib import Path
import json
import logging
import sqlite3
import threading
from unittest.mock import patch

import pytest
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

from eidos_runtime import __version__
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import AssistantMessagePhase
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ModelUsage, ScriptedModel
from eidos_runtime.model.pydantic_ai_client import _model_instrumentation
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.runtime.resource_registry import ResourceRegistry
from eidos_runtime.runtime.supervisor import RunSupervisor
from eidos_runtime.runtime.tool_runtime import ReadOnlyToolHandler
from eidos_runtime.telemetry.provider import initialize_telemetry
from eidos_runtime.telemetry.tracing import start_span


def _install_in_memory_exporter() -> InMemorySpanExporter:
    current = trace.get_tracer_provider()
    if not isinstance(current, TracerProvider):
        current = TracerProvider(
            resource=Resource.create(
                {
                    "service.name": "eidos-runtime",
                    "service.version": __version__,
                }
            )
        )
        trace.set_tracer_provider(current)
    exporter = InMemorySpanExporter()
    current.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


SPAN_EXPORTER = _install_in_memory_exporter()


def _attributes(span: object) -> dict[str, object]:
    return dict(getattr(span, "attributes", {}) or {})


def _finished(name: str) -> list[object]:
    return [span for span in SPAN_EXPORTER.get_finished_spans() if span.name == name]


def setup_function() -> None:
    SPAN_EXPORTER.clear()


def test_native_model_instrumentation_is_opt_in_and_content_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "none")
    assert _model_instrumentation() is False
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "otlp")
    settings = _model_instrumentation()
    assert settings is not False
    assert settings.include_content is False
    assert settings.include_binary_content is False
    assert settings.include_model_request_parameters is False
    monkeypatch.setenv("OTEL_SDK_DISABLED", "1")
    assert _model_instrumentation() is False


def test_provider_supports_standard_exporter_environment_and_safe_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTEL_SERVICE_NAME", "eidos-runtime-test")
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "none")
    provider = initialize_telemetry()
    try:
        assert provider.tracer_provider is not None
        assert provider.tracer_provider.resource.attributes["service.name"] == (
            "eidos-runtime-test"
        )
        assert provider.tracer_provider.resource.attributes["service.version"] == (
            __version__
        )
    finally:
        provider.force_flush()
        provider.shutdown()
        provider.shutdown()


def test_disabled_provider_does_not_raise_business_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    provider = initialize_telemetry()
    provider.force_flush()
    provider.shutdown()
    assert provider.tracer_provider is None


@pytest.mark.parametrize("exporter", ["console", "otlp", "console,otlp"])
def test_provider_initializes_requested_exporter(
    monkeypatch: pytest.MonkeyPatch,
    exporter: str,
) -> None:
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", exporter)
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "http://127.0.0.1:4318/v1/traces",
    )
    provider = initialize_telemetry()
    try:
        assert provider.tracer_provider is not None
        processors = provider.tracer_provider._active_span_processor._span_processors
        exporters = [processor.span_exporter for processor in processors]
        assert len(processors) == len(exporter.split(","))
        if "console" in exporter:
            assert any(
                isinstance(processor, SimpleSpanProcessor)
                and isinstance(processor.span_exporter, ConsoleSpanExporter)
                for processor in processors
            )
        if "otlp" in exporter:
            assert any(
                isinstance(processor, BatchSpanProcessor)
                and isinstance(processor.span_exporter, OTLPSpanExporter)
                for processor in processors
            )
        assert exporters
    finally:
        provider.shutdown()


def test_run_model_and_tool_spans_share_the_run_trace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "safe.txt").write_text("file content", encoding="utf-8")
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    store = SessionStore(data)
    store.initialize()
    session = store.create_session(str(workspace))
    run, _ = store.create_run(session["id"], "user prompt")
    model = ScriptedModel([
        ModelResponse(
            tool_calls=(ModelToolCall("provider-call", "read_file", {"path": "safe.txt"}),),
            provider_name="fixture-provider",
            resolved_model_name="fixture-model",
            finish_reason="tool_calls",
            usage=ModelUsage(input_tokens=11, output_tokens=7),
            transport_retry_count=2,
        ),
        ModelResponse(
            text="done",
            phase=AssistantMessagePhase.FINAL_ANSWER,
            provider_name="fixture-provider",
            resolved_model_name="fixture-model",
            finish_reason="stop",
            usage=ModelUsage(input_tokens=13, output_tokens=3),
        ),
    ])
    try:
        RuntimeEngine(store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )
    finally:
        store.close()

    run_span = _finished("eidos.run")[0]
    model_spans = _finished("eidos.model.attempt")
    tool_span = _finished("eidos.tool.call")[0]
    run_attributes = _attributes(run_span)
    model_attributes = _attributes(model_spans[0])
    tool_attributes = _attributes(tool_span)

    assert run_attributes["eidos.run.id"] == run["id"]
    assert run_attributes["eidos.model.id"] == run["modelId"]
    assert run_attributes["eidos.session.id"] == session["id"]
    assert run_attributes["eidos.run.status"] == "succeeded"
    assert model_attributes["eidos.run.id"] == run["id"]
    assert model_attributes["eidos.step.id"]
    assert model_attributes["eidos.model.provider"] == "fixture-provider"
    assert model_attributes["eidos.model.configured_provider"] == "deepseek"
    assert model_attributes["eidos.model.resolved_name"] == "fixture-model"
    assert model_attributes["eidos.model.finish_reason"] == "tool_calls"
    assert model_attributes["eidos.model.tool_call_count"] == 1
    assert model_attributes["eidos.model.usage.input_tokens"] == 11
    assert model_attributes["eidos.model.usage.output_tokens"] == 7
    assert model_attributes["eidos.model.transport_retry_count"] == 2
    assert tool_attributes["eidos.run.id"] == run["id"]
    assert tool_attributes["eidos.tool.name"] == "read_file"
    assert tool_attributes["eidos.tool.call_id"]
    assert tool_attributes["eidos.tool.status"] == "completed"
    assert tool_attributes["eidos.tool.workspace_changed"] is False
    assert run_span.context.trace_id == model_spans[0].context.trace_id
    assert run_span.context.trace_id == tool_span.context.trace_id
    assert model_spans[0].parent is not None
    assert model_spans[0].parent.span_id == run_span.context.span_id


def test_parallel_tool_spans_inherit_the_run_context(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.txt").write_text("a", encoding="utf-8")
    (workspace / "b.txt").write_text("b", encoding="utf-8")
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    store = SessionStore(data)
    store.initialize()
    session = store.create_session(str(workspace))
    run, _ = store.create_run(session["id"], "parallel user prompt")
    model = ScriptedModel([
        ModelResponse(tool_calls=(
            ModelToolCall("call-a", "read_file", {"path": "a.txt"}),
            ModelToolCall("call-b", "read_file", {"path": "b.txt"}),
        )),
        ModelResponse(text="done", phase=AssistantMessagePhase.FINAL_ANSWER),
    ])
    resources = ResourceRegistry()
    kernel = RuntimeAsyncKernel(resource_registry=resources)
    kernel.start()
    try:
        RuntimeEngine(
            store,
            model,
            lambda _message: None,
            async_kernel=kernel,
            resource_registry=resources,
        ).run(run["id"], threading.Event())
    finally:
        kernel.close()
        store.close()

    run_span = _finished("eidos.run")[0]
    tool_spans = _finished("eidos.tool.call")
    assert len(tool_spans) == 2
    assert {span.context.trace_id for span in tool_spans} == {run_span.context.trace_id}
    assert len({
        _attributes(span)["eidos.tool.call_id"] for span in tool_spans
    }) == 2


def test_model_and_tool_errors_record_exceptions_without_changing_run_result(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data.mkdir(mode=0o700)
    workspace.mkdir()
    store = SessionStore(data)
    store.initialize()
    session = store.create_session(str(workspace))
    model_run, _ = store.create_run(session["id"], "model error")

    class FailingModel:
        def complete(self, *_args: object, **_kwargs: object) -> ModelResponse:
            raise OSError("provider failure")

    try:
        RuntimeEngine(store, FailingModel(), lambda _message: None).run(
            model_run["id"], threading.Event()
        )
        assert store.read_run(model_run["id"])["status"] == "failed"
    finally:
        store.close()

    model_span = _finished("eidos.model.attempt")[0]
    assert model_span.status.status_code.name == "ERROR"
    assert any(event.name == "exception" for event in model_span.events)

    SPAN_EXPORTER.clear()
    data = tmp_path / "tool-data"
    workspace = tmp_path / "tool-workspace"
    data.mkdir(mode=0o700)
    workspace.mkdir()
    store = SessionStore(data)
    store.initialize()
    session = store.create_session(str(workspace))
    tool_run, _ = store.create_run(session["id"], "tool error")
    model = ScriptedModel([
        ModelResponse(tool_calls=(ModelToolCall("call", "read_file", {"path": "x"}),)),
        ModelResponse(text="done", phase=AssistantMessagePhase.FINAL_ANSWER),
    ])
    try:
        with patch.object(ReadOnlyToolHandler, "execute", side_effect=RuntimeError("tool failure")):
            RuntimeEngine(store, model, lambda _message: None).run(
                tool_run["id"], threading.Event()
            )
    finally:
        store.close()

    tool_span = _finished("eidos.tool.call")[0]
    assert tool_span.status.status_code.name == "ERROR"
    assert any(event.name == "exception" for event in tool_span.events)


def test_trace_attributes_do_not_include_sensitive_runtime_content(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "safe.txt").write_text("file content", encoding="utf-8")
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    store = SessionStore(data)
    store.initialize()
    session = store.create_session(str(workspace))
    run, _ = store.create_run(session["id"], "user prompt")
    model = ScriptedModel([
        ModelResponse(tool_calls=(ModelToolCall("call", "read_file", {"path": "safe.txt"}),)),
        ModelResponse(text="done", phase=AssistantMessagePhase.FINAL_ANSWER),
    ])
    try:
        RuntimeEngine(store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )
    finally:
        store.close()

    forbidden = {
        "user prompt",
        "tool arguments",
        "shell command",
        "stdout",
        "stderr",
        "file content",
        "API key",
    }
    for span in SPAN_EXPORTER.get_finished_spans():
        for value in _attributes(span).values():
            assert not any(secret in str(value) for secret in forbidden)


def test_agent_spawn_preserves_parent_trace_across_thread(tmp_path: Path) -> None:
    from eidos_runtime.domain.collaboration import SpawnAgent

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.create_session(str(workspace))
    run, _ = store.create_run(session["id"], "delegate")
    item = store.create_tool_item(run["id"], 1, 0, "spawn-call", "spawn_agent", "{}")
    supervisor = RunSupervisor(
        store,
        lambda _id: ScriptedModel([]),
        lambda _message: None,
        lambda _text: "",
        lambda: True,
        lambda: True,
        lambda: None,
    )
    application = supervisor.collaboration
    application.schedule = lambda: None

    def worker(_run_id: str, _cancel: threading.Event, _gate: threading.Event) -> None:
        with start_span("child.run"):
            pass

    supervisor._run_worker = worker  # type: ignore[method-assign]
    try:
        with start_span("parent.tool") as parent:
            child = application.spawn(
                run["id"], item["id"], SpawnAgent(task_name="inspect", message="inspect")
            )
        start = supervisor._start_worker_locked(child.run_id)
        start.gate.set()
        handle = supervisor._handles[child.run_id]
        handle.thread.join(timeout=5)
        assert not handle.thread.is_alive()
        assert handle.resource is not None
        handle.resource.close()
        child_span = _finished("child.run")[0]
        assert child_span.context.trace_id == parent.get_span_context().trace_id
        assert child_span.parent.span_id == parent.get_span_context().span_id
        assert application.take_trace_context(child.run_id) is None
        supervisor._handles.pop(child.run_id)
        with start_span("unrelated.waker") as waker:
            resumed = supervisor._start_worker_locked(child.run_id)
            resumed.gate.set()
            resumed_handle = supervisor._handles[child.run_id]
            resumed_handle.thread.join(timeout=5)
            assert not resumed_handle.thread.is_alive()
            assert resumed_handle.resource is not None
            resumed_handle.resource.close()
        resumed_span = _finished("child.run")[1]
        assert resumed_span.parent.span_id == parent.get_span_context().span_id
        assert resumed_span.parent.span_id != waker.get_span_context().span_id
    finally:
        supervisor._handles.clear()
        store.close()


def test_local_jsonl_log_records_include_active_trace_ids(tmp_path: Path) -> None:
    from eidos_runtime.db.layout import LOGS_DATABASE_NAME

    data = tmp_path / "data"
    store = SessionStore(data)
    store.initialize()
    handler = store.create_log_handler()
    try:
        with start_span("logged-operation") as span:
            record = logging.LogRecord("eidos.test", logging.INFO, __file__, 1, "work done", (), None)
            handler.handle(record)
        with sqlite3.connect(data / LOGS_DATABASE_NAME) as connection:
            relative_path = connection.execute("SELECT relative_path FROM log_segments").fetchone()[0]
        entry = json.loads((data / "logs" / relative_path).read_text(encoding="utf-8").splitlines()[0])
        assert entry["traceId"] == format(span.get_span_context().trace_id, "032x")
        assert entry["spanId"] == format(span.get_span_context().span_id, "016x")
    finally:
        handler.close()
        store.close()
