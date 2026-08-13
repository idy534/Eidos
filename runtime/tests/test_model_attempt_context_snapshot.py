from __future__ import annotations

from pathlib import Path
import threading

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.context.budget import estimate_context_budget
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.runtime.engine import RuntimeEngine


def _run(store: SessionStore, workspace: Path, user_input: str) -> tuple[str, str]:
    session = store.create_session(str(workspace))
    run, _item = store.create_run(session["id"], user_input)
    return session["id"], run["id"]


def test_every_model_attempt_samples_the_exact_persisted_structured_snapshot(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "main.py").write_text("value = 1\n", encoding="utf-8")
    store = SessionStore(tmp_path / "data")
    store.initialize()
    _session_id, run_id = _run(store, workspace, "inspect the workspace")
    identity = store.workspace_for_run(run_id)
    store.long_task_repository().initialize(
        run_id=run_id,
        workspace_path=str(identity.path),
        workspace_device=identity.device,
        workspace_inode=identity.inode,
        workspace_owner=identity.owner,
    )
    model = ScriptedModel([
        ModelResponse(tool_calls=(ModelToolCall("list", "list_files", {}),)),
        ModelResponse(text="done"),
    ])
    try:
        RuntimeEngine(store, model, lambda _message: None).run(
            run_id, threading.Event()
        )

        attempts = store.read_model_attempts(run_id)
        assert len(attempts) == len(model.contexts) == 2
        for index, attempt in enumerate(attempts):
            assert attempt["contextSnapshotId"] is not None
            snapshot = store.context_snapshot_repository().read_for_model_attempt(
                str(attempt["id"])
            )
            assert snapshot is not None
            assert snapshot.model_context == model.contexts[index]
            assert snapshot.instructions == model.instructions_history[index]
            assert snapshot.tool_definitions == model.tool_definitions_history[index]
            assert snapshot.inventory_snapshot_id is None
            assert snapshot.index_snapshot_id is None
            assert snapshot.retrieval_snapshot_id is None
        assert any(
            item.get("type") == "tool_call" for item in model.contexts[1]
        )
        assert any(
            item.get("type") == "tool_result" for item in model.contexts[1]
        )
        progress = store.long_task_progress(run_id)
        assert progress is not None
        assert progress.context_plan_id == snapshot.plan_id
        assert progress.context_snapshot_id == snapshot.snapshot_id
    finally:
        store.close()


def test_protocol_repair_creates_a_new_exact_context_snapshot(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    _session_id, run_id = _run(store, workspace, "finish")
    model = ScriptedModel([
        ModelResponse(tool_calls=(
            ModelToolCall("invalid", "tool_that_does_not_exist", {}),
        )),
        ModelResponse(text="done"),
    ])
    try:
        RuntimeEngine(store, model, lambda _message: None).run(
            run_id, threading.Event()
        )

        attempts = store.read_model_attempts(run_id)
        assert len(attempts) == 2
        first = store.context_snapshot_repository().read_for_model_attempt(
            str(attempts[0]["id"])
        )
        second = store.context_snapshot_repository().read_for_model_attempt(
            str(attempts[1]["id"])
        )
        assert first is not None and second is not None
        assert first.snapshot_id != second.snapshot_id
        assert second.model_context[-1]["type"] == "protocol_error"
        assert second.model_context == model.contexts[1]
        profile = store.read_model_profile(run_id)
        expected_budget = estimate_context_budget(
            {
                "instructions": second.instructions,
                "messages": second.model_context,
                "tools": [
                    tool.model_dump(mode="json")
                    for tool in second.tool_definitions
                ],
            },
            context_window_tokens=profile.context_window_tokens,
            request_max_output_tokens=profile.max_output_tokens,
            message_count=len(second.model_context),
            tool_call_count=sum(
                item.get("type") == "tool_call"
                for item in second.model_context
            ),
            tool_result_count=sum(
                item.get("type") == "tool_result"
                for item in second.model_context
            ),
        )
        assert second.plan.token_budget == expected_budget
        assert (
            second.plan.token_budget.estimated_input_tokens
            > first.plan.token_budget.estimated_input_tokens
        )
    finally:
        store.close()


def test_transport_retry_metadata_reuses_one_model_attempt_snapshot(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    _session_id, run_id = _run(store, workspace, "finish")
    model = ScriptedModel([
        ModelResponse(
            text="done",
            transport_attempt_count=3,
            transport_retry_count=2,
            last_retry_reason="rate_limited",
        )
    ])
    try:
        RuntimeEngine(store, model, lambda _message: None).run(
            run_id, threading.Event()
        )

        attempts = store.read_model_attempts(run_id)
        assert len(attempts) == 1
        assert attempts[0]["contextSnapshotId"] is not None
        assert store.context_snapshot_repository().read_for_model_attempt(
            str(attempts[0]["id"])
        ) is not None
    finally:
        store.close()
