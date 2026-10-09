import json
from pathlib import Path
import threading

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import ModelProfileSnapshot, ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.model.config import default_profile_snapshot
from eidos_runtime.model.response_phase import AssistantMessagePhase
from eidos_runtime.runtime.engine import RuntimeEngine


def _start(tmp_path: Path) -> tuple[SessionStore, str, str]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "hello.txt").write_text("hello\n")
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.create_session(str(workspace))
    # Older saved snapshots must not reactivate the removed review.
    profile = ModelProfileSnapshot.model_validate({
        **default_profile_snapshot("deepseek-v4.1-flash").model_dump(),
        "completion_check_version": 1,
    })
    run, _ = store.create_run(session["id"], "Inspect the file and answer.", model_id=profile.model_id, model_profile=profile)
    return store, session["id"], run["id"]


def _read() -> ModelResponse:
    return ModelResponse(tool_calls=(ModelToolCall("read", "read_file", {"path": "hello.txt"}),))


def test_tool_answer_finishes_without_extra_model_review(tmp_path: Path) -> None:
    store, session_id, run_id = _start(tmp_path)
    model = ScriptedModel([_read(), ModelResponse(text="The file contains hello.")])
    try:
        RuntimeEngine(store, model, lambda _: None).run(run_id, threading.Event())
        assert store.read_run(run_id)["status"] == "succeeded"
        assert len(model.contexts) == 2
        assert len(store.read_model_attempts(run_id)) == 2
        assert store.database.connection().execute(
            "SELECT count(*) FROM events WHERE run_id=? AND event_type='run.updated' AND json_extract(payload_json,'$.reason')='completion_check'",
            (run_id,),
        ).fetchone()[0] == 0
        messages = [item for item in store.read_session_snapshot(session_id)["items"] if item["kind"] == "assistant_message"]
        assert messages[-1]["status"] == "completed"
    finally:
        store.close()


def test_direct_answer_does_not_require_an_extra_model_request(tmp_path: Path) -> None:
    store, _, run_id = _start(tmp_path)
    model = ScriptedModel([ModelResponse(text="The answer is 42.")])
    try:
        RuntimeEngine(store, model, lambda _: None).run(run_id, threading.Event())
        assert store.read_run(run_id)["status"] == "succeeded"
        assert len(model.contexts) == 1
    finally:
        store.close()


def test_native_continuation_after_tools_keeps_the_run_active(tmp_path: Path) -> None:
    store, _, run_id = _start(tmp_path)
    model = ScriptedModel([
        _read(),
        ModelResponse(text="Checking the explanation.", phase=AssistantMessagePhase.COMMENTARY,
                      phase_source="provider", end_turn=False),
        ModelResponse(text="The file contains hello.", phase=AssistantMessagePhase.FINAL_ANSWER,
                      phase_source="provider", end_turn=True),
    ])
    try:
        RuntimeEngine(store, model, lambda _: None).run(run_id, threading.Event())
        assert store.read_run(run_id)["status"] == "succeeded"
        assert len(model.contexts) == 3
        assert any(item.get("phase") == "commentary" for item in model.contexts[2])
        assert store.read_model_attempts(run_id)[1]["protocolDiagnostics"]["endTurn"] is False
    finally:
        store.close()


def test_legacy_completion_events_remain_readable_without_feedback_injection(tmp_path: Path) -> None:
    store, _, run_id = _start(tmp_path)
    from eidos_runtime.db.events import append_event
    from eidos_runtime.db.database import now_ms
    from eidos_runtime.runtime.state_machine import EventType

    with store.database.connection() as connection:
        append_event(connection, EventType.RUN_UPDATED, now_ms(), {
            "reason": "completion_check",
            "completionCheck": {
                "schemaVersion": 1, "fingerprint": "a" * 64,
                "candidateItemId": "historical-answer", "candidateSha256": "b" * 64,
                "modelAttemptId": "historical-attempt", "status": "continue",
                "reason": "assessed", "remainingWork": ("Old review feedback",),
            },
        }, session_id=store.read_run(run_id)["sessionId"], run_id=run_id)
    model = ScriptedModel([ModelResponse(text="The current answer.")])
    try:
        RuntimeEngine(store, model, lambda _: None).run(run_id, threading.Event())
        assert store.read_run(run_id)["status"] == "succeeded"
        assert not any(item.get("sectionId") == "completion-feedback" for item in model.contexts[0])
        events = store.database.connection().execute("SELECT payload_json FROM events WHERE run_id=?", (run_id,)).fetchall()
        assert any(json.loads(event[0]).get("reason") == "completion_check" for event in events)
    finally:
        store.close()
