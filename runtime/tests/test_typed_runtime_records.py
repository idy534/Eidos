from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from eidos_runtime.db.database import Database
from eidos_runtime.domain.execution import Item, ModelAttempt, Step
from eidos_runtime.domain.run import Run, RunStatus
from eidos_runtime.domain.tool import Approval, ToolCall
from eidos_runtime.persistence.errors import PersistenceCorruptionError
from eidos_runtime.persistence.mappers.runtime import (
    approval_from_row,
    item_from_row,
    model_attempt_from_row,
    run_from_row,
    step_from_row,
    tool_call_from_row,
)
from eidos_runtime.persistence.repositories import TypedRuntimeRepository


def _run_row() -> dict[str, object]:
    return {
        "id": "run-1",
        "session_id": "session-1",
        "user_input": "Inspect the repository",
        "model_id": "model-1",
        "status": "running",
        "model_step_count": 2,
        "reconciliation_required": 0,
        "side_effects_may_exist": 1,
        "workspace_version": 3,
        "created_at": 1000,
        "started_at": 1001,
        "updated_at": 1002,
        "completed_at": None,
        "error_code": None,
        "stop_reason": None,
        "cancel_requested_at": None,
        "cancel_completed_at": None,
    }


def test_core_mappers_return_frozen_domain_records_and_preserve_nullable_facts() -> None:
    run = run_from_row(_run_row())
    item = item_from_row({
        "id": "item-1",
        "session_id": "session-1",
        "run_id": "run-1",
        "ordinal": 1,
        "model_step_index": None,
        "kind": "user_message",
        "status": "completed",
        "content": "Inspect the repository",
        "incomplete": 0,
        "created_at": 1000,
        "completed_at": 1001,
    })
    step = step_from_row({
        "id": "step-1",
        "run_id": "run-1",
        "segment_id": "segment-1",
        "ordinal": 0,
        "status": "completed",
        "observed_reconciliation_epoch": 2,
        "resolution_snapshot_id": "step-snapshot-1",
        "tool_snapshot_json": None,
        "tool_set_hash": None,
        "progress_signature_json": None,
        "created_at": 1000,
        "completed_at": 1001,
    })
    tool_call = tool_call_from_row({
        "id": "tool-1",
        "item_id": "item-2",
        "model_step_index": 1,
        "batch_order": 0,
        "provider_call_id": "call-1",
        "tool_name": "read_file",
        "status": "completed",
        "arguments_json": '{"path":"README.md"}',
        "result_json": '{"outcome":"success"}',
        "model_result_json": '{"outcome":"success"}',
        "approval_status": None,
        "approval_decision": None,
        "approval_feedback": None,
        "approval_diff": None,
        "base_sha256": None,
        "provenance_json": None,
        "tool_set_hash": None,
        "started_at": 1000,
        "duration_ms": None,
        "completed_at": 1001,
    })
    approval = approval_from_row({
        "id": "approval-1",
        "tool_call_id": "tool-1",
        "run_id": "run-1",
        "item_id": "item-2",
        "status": "pending",
        "request_hash": "a" * 64,
        "request_json": '{"path":"README.md"}',
        "attempt_ordinal": 0,
        "approval_kind": "tool",
        "decision": None,
        "feedback": None,
        "created_at": 1000,
        "decided_at": None,
    })
    attempt = model_attempt_from_row({
        "id": "attempt-1",
        "step_id": "step-1",
        "ordinal": 0,
        "status": "completed",
        "provider_name": "provider",
        "configured_provider_id": "provider",
        "resolved_model_name": "model-1",
        "finish_reason": "stop",
        "provider_response_id": None,
        "lease_id": None,
        "wire_api": "chat_completions",
        "model_id": "model-1",
            "request_timeout": 30.0,
            "context_snapshot_id": None,
        "retry_decision_json": None,
        "usage_json": None,
        "error_code": None,
        "http_status": None,
        "ttft_ms": None,
        "duration_ms": 10,
        "had_progress": 1,
        "response_state": "complete",
        "phase": "unknown",
        "tool_call_count": 0,
        "response_text_sha256": None,
        "response_text_bytes": 0,
        "protocol_diagnostics_json": None,
        "started_at": 1000,
        "completed_at": 1010,
    })

    assert isinstance(run, Run)
    assert run.status is RunStatus.RUNNING
    assert run.side_effects_may_exist is True
    assert isinstance(item, Item)
    assert item.model_step_index is None
    assert isinstance(step, Step)
    assert isinstance(tool_call, ToolCall)
    assert tool_call.result_json == '{"outcome":"success"}'
    assert isinstance(approval, Approval)
    assert approval.decided_at is None
    assert isinstance(attempt, ModelAttempt)
    assert attempt.completed_at == 1010

    with pytest.raises(ValidationError):
        run.status = RunStatus.FAILED  # type: ignore[misc]


def test_typed_runtime_repository_does_not_expose_sqlite_rows(tmp_path: Path) -> None:
    data = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data.mkdir(mode=0o700)
    workspace.mkdir()
    database = Database(data)
    database.initialize()
    try:
        session_id = "session-1"
        database.connection().execute(
            "INSERT INTO sessions (id, workspace_root, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (session_id, str(workspace), 1000, 1000),
        )
        database.connection().execute(
            """INSERT INTO runs (id, session_id, user_input, model_id, model_profile_json, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("run-1", session_id, "goal", "model-1", "{}", "running", 1000, 1000),
        )
        database.connection().commit()

        repository = TypedRuntimeRepository(database)
        assert isinstance(repository.read_run("run-1"), Run)
        assert repository.read_run("missing") is None
    finally:
        database.close()


@pytest.mark.parametrize("field", ["status", "created_at", "reconciliation_required"])
def test_run_mapper_rejects_corrupted_persisted_values(field: str) -> None:
    row = _run_row()
    row[field] = "invalid"
    with pytest.raises(PersistenceCorruptionError) as captured:
        run_from_row(row)
    assert captured.value.record == "run"
    assert captured.value.field == field
