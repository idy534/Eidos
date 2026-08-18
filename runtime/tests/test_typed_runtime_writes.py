from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.run import Run, RunStatus
from eidos_runtime.domain.session import DeletedSession, Session
from eidos_runtime.domain.tool import Approval, ApprovalStatus


def _store(tmp_path: Path) -> SessionStore:
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    store = SessionStore(data)
    store.initialize()
    assert store.health_state == "ready"
    return store


def test_typed_runtime_repository_exposes_committed_session_writes_without_changing_legacy_store(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = _store(tmp_path)
    try:
        repository = store.typed_runtime_repository()

        created = repository.create_session(
            str(workspace), operation_id=str(uuid4())
        )
        assert isinstance(created.value, Session)
        assert created.value.workspace_root == str(workspace.resolve())
        assert len(created.event_ids) == 1
        assert store.pending_outbox_count() == 1

        renamed = repository.rename_session(created.value.id, "Typed title")
        assert isinstance(renamed.value, Session)
        assert renamed.value.title == "Typed title"
        assert len(renamed.event_ids) == 1

        deleted = repository.delete_session(created.value.id)
        assert isinstance(deleted.value, DeletedSession)
        assert deleted.value.deleted_session_id == created.value.id
        assert deleted.events == ()

        # The legacy facade is intentionally still wire-shaped for callers
        # that have not yet moved to the typed application boundary.
        legacy = store.create_session(str(workspace))
        assert isinstance(legacy, dict)
    finally:
        store.close()


def test_typed_session_repository_can_persist_a_session_without_a_project(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        data = Path(store.data_directory)
        workspace = data / f".{data.name}-projectless" / "chat-workspace"
        workspace.mkdir(parents=True)
        repository = store.typed_runtime_repository()
        created = repository.create_session(str(workspace), projectless=True)
        projection = repository.read_session_projection(created.value.id)

        assert projection is not None
        assert projection.project is None
        assert projection.worktree is None
        assert created.value.execution_mode.value == "local"
    finally:
        store.close()


def test_typed_session_write_replay_does_not_reemit_already_committed_outbox_event(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = _store(tmp_path)
    try:
        repository = store.typed_runtime_repository()
        operation_id = str(uuid4())

        first = repository.create_session(str(workspace), operation_id=operation_id)
        replay = repository.create_session(str(workspace), operation_id=operation_id)

        assert replay.value == first.value
        assert replay.events == ()
        assert store.pending_outbox_count() == 1
    finally:
        store.close()


def test_typed_runtime_repository_returns_typed_run_and_approval_mutations(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = _store(tmp_path)
    try:
        session = store.create_session(str(workspace))
        queued, _ = store.enqueue_run(session["id"], "inspect")
        repository = store.typed_runtime_repository()

        claimed = repository.claim_next_run_committed()
        assert claimed is not None
        assert isinstance(claimed.value, Run)
        assert claimed.value.id == queued["id"]
        assert claimed.value.status is RunStatus.RUNNING
        assert claimed.event_ids

        cancel_requested = repository.request_cancel_committed(queued["id"])
        assert isinstance(cancel_requested.value, Run)
        assert cancel_requested.value.cancel_requested_at is not None

        connection = store.connection
        assert connection is not None
        connection.execute(
            """
            INSERT INTO items (
                id, session_id, run_id, ordinal, kind, status, created_at
            ) VALUES ('item-approval', ?, ?, 2, 'tool_call', 'in_progress', 1000)
            """,
            (session["id"], queued["id"]),
        )
        connection.execute(
            """
            INSERT INTO tool_calls (
                id, item_id, model_step_index, batch_order, provider_call_id,
                tool_name, status, arguments_json, approval_status, started_at
            ) VALUES (
                'tool-approval', 'item-approval', 0, 0, 'provider-approval',
                'write_file', 'running', '{}', 'pending', 1000
            )
            """
        )
        connection.execute(
            """
            INSERT INTO approvals (
                id, tool_call_id, run_id, item_id, status, request_hash,
                request_json, attempt_ordinal, approval_kind, created_at
            ) VALUES (
                'approval-typed', 'tool-approval', ?, 'item-approval', 'pending', ?,
                '{}', 0, 'tool', 1000
            )
            """,
            (queued["id"], "a" * 64),
        )
        connection.execute(
            "UPDATE runs SET status = 'waiting_approval' WHERE id = ?",
            (queued["id"],),
        )
        connection.commit()

        resolved = repository.resolve_approval_committed(
            "item-approval", "approve", None
        )
        assert isinstance(resolved.value, Approval)
        assert resolved.value.status is ApprovalStatus.APPROVED
        assert resolved.event_ids
    finally:
        store.close()
