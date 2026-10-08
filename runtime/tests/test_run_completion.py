import json
from pathlib import Path
import threading

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import ModelProfileSnapshot, ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.model.config import default_profile_snapshot
from eidos_runtime.model.response_phase import AssistantMessagePhase
from eidos_runtime.runtime.engine import RuntimeEngine


def _assessment(state: str, *, remaining: list[str] | None = None, outputs: list[str] | None = None) -> ModelResponse:
    return ModelResponse(tool_calls=(ModelToolCall("assessment", "_eidos_assess_completion", {
        "state": state, "explanation": "Checked the requested work against the available facts.",
        "remainingWork": remaining or [], "requestedOutputs": outputs or [],
    }),))


def _start(tmp_path: Path) -> tuple[SessionStore, str, str]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "hello.txt").write_text("hello\n")
    (workspace / "other.txt").write_text("other evidence\n")
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.create_session(str(workspace))
    profile = ModelProfileSnapshot.model_validate({
        **default_profile_snapshot("deepseek-v4.1-flash").model_dump(),
        "completion_check_version": 1,
    })
    run, _ = store.create_run(session["id"], "Inspect the file and finish the requested work.", model_id=profile.model_id, model_profile=profile)
    return store, session["id"], run["id"]


def _read(call_id: str = "read", path: str = "hello.txt") -> ModelResponse:
    return ModelResponse(tool_calls=(ModelToolCall(call_id, "read_file", {"path": path}),))


def _records(store: SessionStore, run_id: str) -> list[dict]:
    rows = store.database.connection().execute(
        "SELECT payload_json FROM events WHERE run_id=? AND event_type='run.updated' AND json_extract(payload_json,'$.reason')='completion_check' ORDER BY id", (run_id,),
    ).fetchall()
    return [json.loads(row[0])["completionCheck"] for row in rows]


def test_weak_progress_completion_is_checked_and_work_continues(tmp_path: Path) -> None:
    store, session_id, run_id = _start(tmp_path)
    model = ScriptedModel([
        _read(), ModelResponse(text="I will make another inspection."),
        _assessment("continue", remaining=["Inspect the other requested file."]),
        _read("read-other", "other.txt"), ModelResponse(text="The requested inspection is complete."),
        _assessment("complete"),
    ])
    try:
        RuntimeEngine(store, model, lambda _: None).run(run_id, threading.Event())
        assert store.read_run(run_id)["status"] == "succeeded"
        assert len(model.contexts) == 6
        assert [record["status"] for record in _records(store, run_id)] == ["running", "continue", "running", "complete"]
        tools = [i for i in store.read_session_snapshot(session_id)["items"] if "toolCall" in i]
        assert len(tools) == 2  # The assessment is a model output schema, never an executed tool.
        assert any("completion-feedback" == item.get("sectionId") for item in model.contexts[3])
        for attempt in store.read_model_attempts(run_id):
            assert store.context_snapshot_repository().read_for_model_attempt(attempt["id"]) is not None
    finally:
        store.close()


def test_direct_answer_does_not_require_an_extra_model_request(tmp_path: Path) -> None:
    store, _, run_id = _start(tmp_path)
    model = ScriptedModel([ModelResponse(text="The answer is 42.")])
    try:
        RuntimeEngine(store, model, lambda _: None).run(run_id, threading.Event())
        assert store.read_run(run_id)["status"] == "succeeded"
        assert len(model.contexts) == 1
        assert _records(store, run_id) == []
    finally:
        store.close()


def test_explicit_provider_end_does_not_need_semantic_confirmation(tmp_path: Path) -> None:
    store, _, run_id = _start(tmp_path)
    model = ScriptedModel([_read(), ModelResponse(text="Done.", end_turn=True)])
    try:
        RuntimeEngine(store, model, lambda _: None).run(run_id, threading.Event())
        assert store.read_run(run_id)["status"] == "succeeded"
        assert len(model.contexts) == 2
        assert _records(store, run_id) == []
    finally:
        store.close()


def test_progress_wording_does_not_reset_completion_check_without_new_facts(tmp_path: Path) -> None:
    store, _, run_id = _start(tmp_path)
    model = ScriptedModel([
        _read(), ModelResponse(text="I will continue."),
        _assessment("continue", remaining=["Perform the remaining work."]),
        ModelResponse(text="Next I will perform the remaining work."),
    ])
    try:
        RuntimeEngine(store, model, lambda _: None).run(run_id, threading.Event())
        assert store.read_run(run_id)["status"] == "stopped"
        assert store.read_run(run_id)["stopReason"] == "completion_unconfirmed"
        assert len(model.contexts) == 4
        assert len(_records(store, run_id)) == 2
    finally:
        store.close()


def test_invalid_assessment_retains_candidate_and_stops_honestly(tmp_path: Path) -> None:
    store, session_id, run_id = _start(tmp_path)
    model = ScriptedModel([_read(), ModelResponse(text="A candidate result."), ModelResponse(text="Maybe finished.")])
    try:
        RuntimeEngine(store, model, lambda _: None).run(run_id, threading.Event())
        assert store.read_run(run_id)["status"] == "stopped"
        messages = [i["content"] for i in store.read_session_snapshot(session_id)["items"] if i["kind"] == "assistant_message"]
        assert messages == ["A candidate result."]
        assert _records(store, run_id)[-1]["status"] == "failed"
    finally:
        store.close()


def test_native_continuation_and_phase_survive_persisted_history(tmp_path: Path) -> None:
    store, _, run_id = _start(tmp_path)
    model = ScriptedModel([
        ModelResponse(text="Checking.", phase=AssistantMessagePhase.COMMENTARY, phase_source="provider", end_turn=False),
        ModelResponse(text="Done.", phase=AssistantMessagePhase.FINAL_ANSWER, phase_source="provider", end_turn=True),
    ])
    try:
        RuntimeEngine(store, model, lambda _: None).run(run_id, threading.Event())
        assert store.read_run(run_id)["status"] == "succeeded"
        assert len(model.contexts) == 2
        assert any(item.get("phase") == "commentary" for item in model.contexts[1])
        attempts = store.read_model_attempts(run_id)
        assert attempts[0]["protocolDiagnostics"]["endTurn"] is False
        assert attempts[1]["protocolDiagnostics"]["endTurn"] is True
    finally:
        store.close()


def test_declared_user_outputs_are_required_when_assessment_claims_delivery(tmp_path: Path) -> None:
    store, session_id, run_id = _start(tmp_path)
    workspace = Path(store.read_session(session_id)["workspaceRoot"])
    (workspace / "result.txt").write_text("deliverable")
    model = ScriptedModel([
        _read(), ModelResponse(text="The deliverable is ready."),
        _assessment("complete", outputs=["result.txt"]),
        ModelResponse(tool_calls=(ModelToolCall("declare", "declare_outputs", {"outputs": [{"path": "result.txt"}]}),)),
        ModelResponse(text="The deliverable is now registered."),
        _assessment("complete", outputs=["result.txt"]),
    ])
    try:
        RuntimeEngine(store, model, lambda _: None).run(run_id, threading.Event())
        assert store.read_run(run_id)["status"] == "succeeded"
        records = _records(store, run_id)
        assert records[1]["reason"] == "outputs_not_declared_or_changed"
        assert records[-1]["status"] == "complete"
    finally:
        store.close()
