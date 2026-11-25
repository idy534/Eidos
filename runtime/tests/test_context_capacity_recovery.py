from __future__ import annotations

from pathlib import Path
import threading

import pytest

from eidos_runtime.context.builder import ContextBuilder
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.model.config import default_profile_snapshot
from eidos_runtime.runtime.engine import RuntimeEngine


class ImmediateCancel(threading.Event):
    """Recovery waits one second before the next request; tests do not."""

    def wait(self, timeout: float | None = None) -> bool:
        return self.is_set()


@pytest.fixture
def workspace_store(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("fixture\n", encoding="utf-8")
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.create_session(str(workspace))
    try:
        yield store, session
    finally:
        store.close()


def _sections(context) -> set[str]:
    return {str(item["sectionId"]) for item in context if item.get("sectionId")}


def _tool_results(context) -> set[str]:
    return {str(item.get("callId")) for item in context if item.get("type") == "tool_result"}


def _feedback(context) -> str:
    return "\n".join(
        str(item.get("content")) for item in context
        if item.get("sectionId") == "runtime-feedback"
    )


def test_repeated_output_limit_keeps_the_whole_history(workspace_store):
    store, session = workspace_store
    run, _ = store.create_run(session["id"], "Read the file, then write the report")
    model = ScriptedModel([
        ModelResponse(tool_calls=(ModelToolCall("read-1", "read_file", {"path": "README.md"}),)),
        ModelResponse(text="first", finish_reason="length"),
        ModelResponse(text="second", finish_reason="length"),
        ModelResponse(text="third", finish_reason="length"),
        ModelResponse(text="A shorter report."),
    ])
    engine = RuntimeEngine(store, model, lambda _message: None)

    engine.run(run["id"], ImmediateCancel())

    assert store.read_run(run["id"])["status"] == "succeeded"
    assert [attempt["status"] for attempt in store.read_model_attempts(run["id"])] == [
        "completed", "failed", "failed", "failed", "completed",
    ]
    # The output limit never moves the Run into the reduced recovery view.
    assert engine.context_recovery_level == 0
    for context in model.contexts[1:]:
        assert "read-1" in _tool_results(context)
        assert "saved-tool-evidence" not in _sections(context)
    assert {"type": "protocol_error", "code": "length"} in model.contexts[2]
    for context in model.contexts[3:]:
        assert "model_output_capacity" in _feedback(context)
    assert "model_output_capacity" not in _feedback(model.contexts[2])


def _capacity_pressure(monkeypatch, store, run_id, *, failed_tokens: int, later_tokens: int):
    """Report a Provider-measured full view that does not fit until a tool has run."""

    build = ContextBuilder.build

    def with_measurement(builder, target_run_id, **options):
        built = build(builder, target_run_id, **options)
        if options.get("recovery_level", 0):
            return built
        ran_tool = any(
            item.provider_call_id is not None
            for item in store.context_projection_facts(run_id).items
        )
        tokens = later_tokens if ran_tool else failed_tokens
        budget = built.budget.model_copy(update={
            "projected_input_tokens": tokens, "measurement": "provider",
            "fits": tokens <= built.budget.usable_input_budget,
        })
        return built.model_copy(update={"budget": budget})

    monkeypatch.setattr(ContextBuilder, "build", with_measurement)


def _recovering_run(store, session):
    # Usable input budget: 100,000 - 8,192 - 2,000 = 89,808 tokens.
    profile = default_profile_snapshot("deepseek-flash").model_copy(update={
        "context_window_tokens": 100_000, "max_output_tokens": 8_192,
    })
    run, _ = store.create_run(session["id"], "Inspect the project", model_profile=profile)
    model = ScriptedModel([
        ModelResponse(tool_calls=(ModelToolCall("read-1", "read_file", {"path": "README.md"}),)),
        ModelResponse(text="Done"),
    ])
    return run, model


def test_capacity_recovery_returns_to_the_full_history_once_it_is_smaller(
    workspace_store, monkeypatch,
):
    store, session = workspace_store
    run, model = _recovering_run(store, session)
    # 95,000 tokens do not fit and nothing can be summarized. 60,000 later fit
    # and are below three quarters of the size that failed (71,250).
    _capacity_pressure(monkeypatch, store, run["id"], failed_tokens=95_000, later_tokens=60_000)
    engine = RuntimeEngine(store, model, lambda _message: None)

    engine.run(run["id"], ImmediateCancel())

    assert store.read_run(run["id"])["status"] == "succeeded"
    recovering, restored = model.contexts
    assert "context_window_capacity" in _feedback(recovering)
    assert "read-1" in _tool_results(restored)
    assert "saved-tool-evidence" not in _sections(restored)
    assert engine.context_recovery_level == 0
    assert engine.context_recovery_ceilings == []


def test_capacity_recovery_stays_reduced_near_the_size_that_failed(
    workspace_store, monkeypatch,
):
    store, session = workspace_store
    run, model = _recovering_run(store, session)
    # 80,000 tokens fit the budget, but a view this close to the failed 95,000
    # is not sent again: the reduced view stays in place.
    _capacity_pressure(monkeypatch, store, run["id"], failed_tokens=95_000, later_tokens=80_000)
    engine = RuntimeEngine(store, model, lambda _message: None)

    engine.run(run["id"], ImmediateCancel())

    assert store.read_run(run["id"])["status"] == "succeeded"
    _recovering, reduced = model.contexts
    assert _tool_results(reduced) == set()
    assert "saved-tool-evidence" in _sections(reduced)
    assert engine.context_recovery_level == 1
    assert engine.context_recovery_ceilings == [95_000]


def test_capacity_recovery_caps_at_max_level_with_hard_floor_fallback(
    workspace_store, monkeypatch,
):
    from eidos_runtime.context.builder import MAX_CONTEXT_RECOVERY_LEVEL
    from eidos_runtime.domain.capacity import CapacityExceeded
    from eidos_runtime.runtime.engine import _recovery_backoff_seconds

    store, session = workspace_store
    (store.workspace_for_session(session["id"]) if hasattr(store, "workspace_for_session") else None)
    long_prompt = "Context overflow prompt. " * 200
    run, _ = store.create_run(session["id"], long_prompt)
    store.create_tool_item(run["id"], 0, 0, "c-1", "read_file", '{"path": "README.md"}')
    builder = ContextBuilder(store)
    level_7 = builder.build(run["id"], recovery_level=7)
    level_8 = builder.build(run["id"], recovery_level=MAX_CONTEXT_RECOVERY_LEVEL)
    assert MAX_CONTEXT_RECOVERY_LEVEL == 8
    assert level_8.budget.projected_input_tokens < level_7.budget.projected_input_tokens
    assert "saved-tool-evidence" not in _sections(level_8.model_context)

    assert _recovery_backoff_seconds(1, first_delay=1.0) == 1.0
    assert _recovery_backoff_seconds(1, first_delay=0.0) == 0.0
    assert _recovery_backoff_seconds(2) == 1.0
    assert _recovery_backoff_seconds(3) == 2.0
    assert _recovery_backoff_seconds(4) == 4.0
    assert _recovery_backoff_seconds(7) == 30.0
    assert _recovery_backoff_seconds(12) == 30.0

    waits: list[float] = []

    class RecordingCancel(threading.Event):
        def wait(self, timeout: float | None = None) -> bool:
            if timeout is not None:
                waits.append(float(timeout))
            if len(waits) >= 10:
                self.set()
            return self.is_set()

    build = ContextBuilder.build

    def always_over_budget(self, target_run_id, **options):
        built = build(self, target_run_id, **options)
        if options.get("recovery_level", 0) == 0 or len(waits) < 10:
            raise CapacityExceeded("context_window_capacity")
        return built

    monkeypatch.setattr(ContextBuilder, "build", always_over_budget)
    engine = RuntimeEngine(store, ScriptedModel([]), lambda _message: None)
    cancel = RecordingCancel()
    engine.run(run["id"], cancel)
    assert store.read_run(run["id"])["status"] == "canceled"
    assert engine.context_recovery_level == MAX_CONTEXT_RECOVERY_LEVEL
    assert len(engine.context_recovery_ceilings) == MAX_CONTEXT_RECOVERY_LEVEL
    assert waits == [1.0, 1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0, 30.0]


def test_repeated_recovery_loops_apply_cancelable_backoff(workspace_store):
    store, session = workspace_store
    run, _ = store.create_run(session["id"], "Test loop backoff")
    waits: list[float] = []

    class RecordingCancel(threading.Event):
        def wait(self, timeout: float | None = None) -> bool:
            if timeout is not None:
                waits.append(float(timeout))
            if len(waits) >= 4:
                self.set()
                return True
            return False

    responses = [ModelResponse(text="x", finish_reason="length") for _ in range(8)]
    engine = RuntimeEngine(store, ScriptedModel(responses), lambda _message: None)
    engine.run(run["id"], RecordingCancel())
    assert store.read_run(run["id"])["status"] == "canceled"
    assert waits == [1.0, 1.0, 2.0, 4.0]
