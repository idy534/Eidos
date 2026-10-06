from __future__ import annotations

import threading
from pathlib import Path

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.runtime.lifecycle import LoopLifecycleEvent, LoopStage


def _run(tmp_path: Path, observer) -> tuple[list[LoopLifecycleEvent], str]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "hello.txt").write_text("hello\n", encoding="utf-8")
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.create_session(str(workspace))
    run, _ = store.create_run(session["id"], "Read hello.txt")
    events: list[LoopLifecycleEvent] = []

    def record(event: LoopLifecycleEvent) -> None:
        events.append(event)
        observer(event)

    try:
        RuntimeEngine(
            store,
            ScriptedModel([
                ModelResponse(tool_calls=(
                    ModelToolCall("call-1", "read_file", {"path": "hello.txt"}),
                )),
                ModelResponse(text="done"),
            ]),
            lambda _message: None,
            lifecycle_observer=record,
        ).run(run["id"], threading.Event())
        return events, str(store.read_run(run["id"])["status"])
    finally:
        store.close()


def test_loop_lifecycle_order_and_identity(tmp_path: Path) -> None:
    events, status = _run(tmp_path, lambda _event: None)
    assert status == "succeeded"
    assert [event.stage for event in events] == [
        LoopStage.BEFORE_MODEL,
        LoopStage.AFTER_MODEL,
        LoopStage.BEFORE_TOOL_BATCH,
        LoopStage.AFTER_TOOL_BATCH,
        LoopStage.BEFORE_MODEL,
        LoopStage.AFTER_MODEL,
        LoopStage.RUN_EXITED,
    ]
    assert events[0].step_id == events[1].step_id == events[2].step_id
    assert events[0].model_attempt_id == events[1].model_attempt_id
    assert events[2].tool_count == events[3].tool_count == 1
    assert events[-1].reason == "succeeded"


def test_diagnostic_observer_failure_does_not_change_run(tmp_path: Path) -> None:
    def fail(_event: LoopLifecycleEvent) -> None:
        raise RuntimeError("diagnostic observer unavailable")

    events, status = _run(tmp_path, fail)
    assert status == "succeeded"
    assert events[-1].stage == LoopStage.RUN_EXITED
