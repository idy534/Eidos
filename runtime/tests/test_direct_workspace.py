from __future__ import annotations

from pathlib import Path
import subprocess
import threading
from uuid import uuid4

import pytest

from eidos_runtime.application.checkpoints import CheckpointApplication
from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.application.worktree_retention import WorktreeRetentionService
from eidos_runtime.domain.worktree import (
    WorktreeLifecycleOperation,
    WorktreeLifecycleScope,
    WorktreeLifecycleState,
)
from eidos_runtime.git.errors import WorktreeError
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.git import WorktreeManager
from eidos_runtime.protocol.methods import (
    CheckpointCreateRequestDto,
    CheckpointForkRequestDto,
    CheckpointRewindRequestDto,
    SessionCreateRequestDto,
    SessionDeleteRequestDto,
    SessionListRequestDto,
)
from eidos_runtime.tools.runtime_workspace import ToolExecutor


def test_non_git_directory_creates_a_direct_workspace_session(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "report"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    manager = WorktreeManager(
        store.database,
        managed_root=tmp_path / "managed-worktrees",
    )
    application = SessionApplication(
        store,
        scan_text=lambda value: value,
        worktree_manager=manager,
    )

    try:
        created = application.create(
            SessionCreateRequestDto(workspaceRoot=str(workspace))
        ).root

        assert created["project"]["workspaceRoot"] == str(workspace.resolve())
        assert created["project"]["gitAvailable"] is False
        assert created.get("worktree") is None
        session_row = store.connection.execute(
            "SELECT worktree_id FROM sessions WHERE id = ?",
            (created["id"],),
        ).fetchone()
        assert session_row["worktree_id"] is None
    finally:
        store.close()


def _setup(tmp_path: Path) -> tuple[SessionStore, WorktreeManager, SessionApplication]:
    store = SessionStore(tmp_path / "data")
    store.initialize()
    manager = WorktreeManager(
        store.database,
        managed_root=tmp_path / "managed-worktrees",
    )
    return store, manager, SessionApplication(
        store,
        scan_text=lambda value: value,
        worktree_manager=manager,
    )


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _git_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q", "-b", "main")
    _git(repository, "config", "user.name", "Eidos Tests")
    _git(repository, "config", "user.email", "eidos-tests@example.com")
    (repository / "README.md").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-qm", "initial")
    return repository


def test_direct_threads_share_project_identity_and_survive_restart(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "report"
    workspace.mkdir()
    store, _manager, application = _setup(tmp_path)
    try:
        first = application.create(
            SessionCreateRequestDto(workspaceRoot=str(workspace))
        ).root
        second = application.create(
            SessionCreateRequestDto(workspaceRoot=str(workspace))
        ).root
        assert first["project"]["id"] == second["project"]["id"]
        assert first["id"] != second["id"]
        assert store.connection.execute(
            "SELECT COUNT(*) FROM projects WHERE workspace_root = ?",
            (str(workspace.resolve()),),
        ).fetchone()[0] == 1
    finally:
        store.close()

    restarted = SessionStore(tmp_path / "data")
    restarted.initialize()
    manager = WorktreeManager(
        restarted.database,
        managed_root=tmp_path / "managed-worktrees",
    )
    application = SessionApplication(
        restarted,
        scan_text=lambda value: value,
        worktree_manager=manager,
    )
    try:
        sessions = application.list(SessionListRequestDto()).root["items"]
        assert {item["project"]["id"] for item in sessions} == {
            first["project"]["id"]
        }
        assert {item["workspaceRoot"] for item in sessions} == {
            str(workspace.resolve())
        }
        assert all(item.get("worktree") is None for item in sessions)
    finally:
        restarted.close()


def test_direct_run_freezes_filesystem_identity_and_keeps_tools_available(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "report"
    workspace.mkdir()
    store, _manager, application = _setup(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(workspaceRoot=str(workspace))
        ).root
        run, _item = store.create_run(str(session["id"]), "inspect")
        identity = store.workspace_for_run(str(run["id"]))
        assert identity.path == workspace.resolve()
        assert identity.git_dir is None
        assert identity.git_common_dir is None
        with ToolExecutor(identity) as executor:
            (workspace / "input.txt").write_text("hello\n", encoding="utf-8")
            read = executor.execute_read(
                "read_file", "read", {"path": "input.txt"}, threading.Event()
            )
            assert read["data"]["content"] == "hello\n"
            change = executor.prepare_file_change(
                "write_file",
                {"path": "output.txt", "content": "written\n"},
                threading.Event(),
            )
            assert not isinstance(change, dict)
            executor.commit_file_change("write_file", change, threading.Event())
            shell = executor.prepare_shell(".", threading.Event())
            assert shell.path == workspace.resolve()
        assert (workspace / "output.txt").read_text(encoding="utf-8") == "written\n"
    finally:
        store.close()


def test_direct_delete_removes_only_session_data(tmp_path: Path) -> None:
    workspace = tmp_path / "report"
    workspace.mkdir()
    marker = workspace / "keep.txt"
    marker.write_text("keep\n", encoding="utf-8")
    store, _manager, application = _setup(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(workspaceRoot=str(workspace))
        ).root
        application.delete(SessionDeleteRequestDto(sessionId=str(session["id"])))
        assert store.connection.execute(
            "SELECT COUNT(*) FROM sessions WHERE id = ?", (session["id"],)
        ).fetchone()[0] == 0
        assert workspace.is_dir()
        assert marker.read_text(encoding="utf-8") == "keep\n"
    finally:
        store.close()


def test_direct_checkpoint_and_fork_share_workspace_without_git_state(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "report"
    workspace.mkdir()
    store, manager, application = _setup(tmp_path)
    checkpoints = CheckpointApplication(
        store,
        store.checkpoint_repository(),
        worktree_manager=manager,
    )
    try:
        parent = application.create(
            SessionCreateRequestDto(workspaceRoot=str(workspace))
        ).root
        run, _item = store.enqueue_run(str(parent["id"]), "continue")
        checkpoint = checkpoints.create(
            CheckpointCreateRequestDto(runId=str(run["id"]))
        ).checkpoint
        assert checkpoint.git_head is None
        assert checkpoint.workspace_identity_hash

        fork = checkpoints.fork(
            CheckpointForkRequestDto(checkpointId=checkpoint.id)
        )
        parent_projection = store.typed_runtime_repository().read_session_projection(
            str(parent["id"])
        )
        fork_run = store.read_run(fork.run.id)
        fork_projection = store.typed_runtime_repository().read_session_projection(
            str(fork_run["sessionId"])
        )
        assert parent_projection is not None
        assert fork_projection is not None
        assert fork_projection.session.id != parent_projection.session.id
        assert fork_projection.session.worktree_id is None
        assert fork_projection.project.id == parent_projection.project.id
        assert fork_projection.project.workspace_root == str(workspace.resolve())
        assert store.connection.execute(
            "SELECT COUNT(*) FROM checkpoint_actions WHERE checkpoint_id = ?",
            (checkpoint.id,),
        ).fetchone()[0] == 1
    finally:
        store.close()


def test_local_git_checkpoint_rewind_restores_exact_workspace_state(
    tmp_path: Path,
) -> None:
    repository = _git_repository(tmp_path)
    store, manager, application = _setup(tmp_path)
    checkpoints = CheckpointApplication(
        store,
        store.checkpoint_repository(),
        worktree_manager=manager,
        retention=WorktreeRetentionService(store.database, manager),
    )
    try:
        session = application.create(
            SessionCreateRequestDto(workspaceRoot=str(repository))
        ).root
        (repository / "README.md").write_text("staged\n", encoding="utf-8")
        _git(repository, "add", "README.md")
        (repository / "README.md").write_text(
            "staged\nunstaged\n", encoding="utf-8"
        )
        (repository / "untracked.txt").write_text("checkpoint\n", encoding="utf-8")
        run, _item = store.enqueue_run(str(session["id"]), "checkpoint")
        checkpoint = checkpoints.create(CheckpointCreateRequestDto(
            runId=str(run["id"]), operationId=str(uuid4()),
        )).checkpoint
        assert checkpoint.git_snapshot_id is not None
        store.connection.execute(
            "UPDATE runs SET status = 'succeeded' WHERE id = ?", (run["id"],)
        )

        (repository / "README.md").write_text("later\n", encoding="utf-8")
        _git(repository, "add", "README.md")
        _git(repository, "commit", "-qm", "later")
        (repository / "untracked.txt").unlink()
        (repository / "later.txt").write_text("later\n", encoding="utf-8")
        request = CheckpointRewindRequestDto(
            checkpointId=checkpoint.id, operationId=str(uuid4()),
        )

        first = checkpoints.rewind(request)
        replay = checkpoints.rewind(request)

        assert replay == first
        assert _git(repository, "rev-parse", "HEAD") == checkpoint.git_head
        assert _git(repository, "show", ":README.md") == "staged"
        assert (repository / "README.md").read_text(encoding="utf-8") == (
            "staged\nunstaged\n"
        )
        assert (repository / "untracked.txt").read_text(encoding="utf-8") == (
            "checkpoint\n"
        )
        assert not (repository / "later.txt").exists()
        assert store.connection.execute(
            "SELECT COUNT(*) FROM checkpoint_actions WHERE action = 'rewind'"
        ).fetchone()[0] == 1
    finally:
        store.close()


def test_local_git_checkpoint_rewind_rejects_active_run_before_prepare(
    tmp_path: Path,
) -> None:
    repository = _git_repository(tmp_path)
    store, manager, application = _setup(tmp_path)
    checkpoints = CheckpointApplication(
        store,
        store.checkpoint_repository(),
        worktree_manager=manager,
        retention=WorktreeRetentionService(store.database, manager),
    )
    try:
        session = application.create(
            SessionCreateRequestDto(workspaceRoot=str(repository))
        ).root
        run, _item = store.enqueue_run(str(session["id"]), "checkpoint")
        checkpoint = checkpoints.create(CheckpointCreateRequestDto(
            runId=str(run["id"]), operationId=str(uuid4()),
        )).checkpoint
        store.connection.execute(
            "UPDATE runs SET status = 'succeeded' WHERE id = ?", (run["id"],)
        )
        sibling = application.create(
            SessionCreateRequestDto(workspaceRoot=str(repository))
        ).root
        store.enqueue_run(str(sibling["id"]), "active sibling")

        with pytest.raises(ApplicationError) as busy:
            checkpoints.rewind(CheckpointRewindRequestDto(
                checkpointId=checkpoint.id, operationId=str(uuid4()),
            ))

        assert busy.value.code == "CHECKPOINT_WORKFLOW_BUSY"
        assert store.connection.execute(
            "SELECT COUNT(*) FROM worktree_lifecycle_operations "
            "WHERE scope = 'checkpoint/rewind'"
        ).fetchone()[0] == 0
    finally:
        store.close()


def test_local_git_checkpoint_rewind_rejects_replaced_workspace_identity(
    tmp_path: Path,
) -> None:
    repository = _git_repository(tmp_path)
    store, manager, application = _setup(tmp_path)
    checkpoints = CheckpointApplication(
        store,
        store.checkpoint_repository(),
        worktree_manager=manager,
        retention=WorktreeRetentionService(store.database, manager),
    )
    try:
        session = application.create(
            SessionCreateRequestDto(workspaceRoot=str(repository))
        ).root
        run, _item = store.enqueue_run(str(session["id"]), "checkpoint")
        checkpoint = checkpoints.create(CheckpointCreateRequestDto(
            runId=str(run["id"]), operationId=str(uuid4()),
        )).checkpoint
        store.connection.execute(
            "UPDATE runs SET status = 'succeeded' WHERE id = ?", (run["id"],)
        )
        moved = repository.with_name("repository-moved")
        repository.rename(moved)
        repository.mkdir()

        with pytest.raises(ApplicationError) as invalid:
            checkpoints.rewind(CheckpointRewindRequestDto(
                checkpointId=checkpoint.id, operationId=str(uuid4()),
            ))

        assert invalid.value.code == "CHECKPOINT_GIT_STATE_UNAVAILABLE"
        assert store.connection.execute(
            "SELECT COUNT(*) FROM worktree_lifecycle_operations "
            "WHERE scope = 'checkpoint/rewind'"
        ).fetchone()[0] == 0
    finally:
        store.close()


def test_local_git_checkpoint_rewind_rejects_unfinished_merge_before_prepare(
    tmp_path: Path,
) -> None:
    repository = _git_repository(tmp_path)
    store, manager, application = _setup(tmp_path)
    checkpoints = CheckpointApplication(
        store,
        store.checkpoint_repository(),
        worktree_manager=manager,
        retention=WorktreeRetentionService(store.database, manager),
    )
    try:
        session = application.create(
            SessionCreateRequestDto(workspaceRoot=str(repository))
        ).root
        run, _item = store.enqueue_run(str(session["id"]), "checkpoint")
        checkpoint = checkpoints.create(CheckpointCreateRequestDto(
            runId=str(run["id"]), operationId=str(uuid4()),
        )).checkpoint
        store.connection.execute(
            "UPDATE runs SET status = 'succeeded' WHERE id = ?", (run["id"],)
        )
        _git(repository, "switch", "-qc", "side")
        (repository / "README.md").write_text("side\n", encoding="utf-8")
        _git(repository, "commit", "-qam", "side")
        _git(repository, "switch", "-q", "main")
        (repository / "README.md").write_text("main\n", encoding="utf-8")
        _git(repository, "commit", "-qam", "main")
        conflict = subprocess.run(
            ["git", "merge", "side"],
            cwd=repository,
            check=False,
            capture_output=True,
            text=True,
        )
        assert conflict.returncode != 0

        with pytest.raises(ApplicationError) as in_progress:
            checkpoints.rewind(CheckpointRewindRequestDto(
                checkpointId=checkpoint.id, operationId=str(uuid4()),
            ))

        assert in_progress.value.code == "GIT_OPERATION_IN_PROGRESS"
        assert manager.local_operation_state(repository).value == "merge"
        assert store.connection.execute(
            "SELECT COUNT(*) FROM worktree_lifecycle_operations "
            "WHERE scope = 'checkpoint/rewind'"
        ).fetchone()[0] == 0
    finally:
        store.close()


def test_local_git_checkpoint_rewind_failure_requires_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _git_repository(tmp_path)
    store, manager, application = _setup(tmp_path)
    checkpoints = CheckpointApplication(
        store,
        store.checkpoint_repository(),
        worktree_manager=manager,
        retention=WorktreeRetentionService(store.database, manager),
    )
    try:
        session = application.create(
            SessionCreateRequestDto(workspaceRoot=str(repository))
        ).root
        run, _item = store.enqueue_run(str(session["id"]), "checkpoint")
        checkpoint = checkpoints.create(CheckpointCreateRequestDto(
            runId=str(run["id"]), operationId=str(uuid4()),
        )).checkpoint
        store.connection.execute(
            "UPDATE runs SET status = 'succeeded' WHERE id = ?", (run["id"],)
        )

        def fail_restore(*_args: object, **_kwargs: object) -> None:
            raise WorktreeError("worktree_restore_failed")

        monkeypatch.setattr(manager, "restore_local_snapshot_state", fail_restore)
        with pytest.raises(ApplicationError) as failed:
            checkpoints.rewind(CheckpointRewindRequestDto(
                checkpointId=checkpoint.id, operationId=str(uuid4()),
            ))

        assert failed.value.code == "CHECKPOINT_REWIND_FAILED"
        stored = store.checkpoint_repository().read(checkpoint.id)
        assert stored is not None and stored.reconciliation_required
        lifecycle = store.connection.execute(
            "SELECT state FROM worktree_lifecycle_operations "
            "WHERE scope = 'checkpoint/rewind'"
        ).fetchone()
        assert lifecycle is not None and lifecycle["state"] == "cleanup_required"
    finally:
        store.close()


def test_local_git_checkpoint_rewind_resumes_after_runtime_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _git_repository(tmp_path)
    store, manager, application = _setup(tmp_path)
    checkpoints = CheckpointApplication(
        store,
        store.checkpoint_repository(),
        worktree_manager=manager,
        retention=WorktreeRetentionService(store.database, manager),
    )
    try:
        session = application.create(
            SessionCreateRequestDto(workspaceRoot=str(repository))
        ).root
        (repository / "README.md").write_text("checkpoint\n", encoding="utf-8")
        run, _item = store.enqueue_run(str(session["id"]), "checkpoint")
        checkpoint = checkpoints.create(CheckpointCreateRequestDto(
            runId=str(run["id"]), operationId=str(uuid4()),
        )).checkpoint
        store.connection.execute(
            "UPDATE runs SET status = 'succeeded' WHERE id = ?", (run["id"],)
        )
        (repository / "README.md").write_text("later\n", encoding="utf-8")
        operation_id = str(uuid4())
        request = CheckpointRewindRequestDto(
            checkpointId=checkpoint.id, operationId=operation_id,
        )
        original_update = manager.lifecycle.update_state
        interrupted = False

        def interrupt_after_git(
            scope: WorktreeLifecycleScope | str,
            candidate_operation_id: str,
            state: WorktreeLifecycleState,
            **kwargs: object,
        ) -> WorktreeLifecycleOperation:
            nonlocal interrupted
            if not interrupted and state is WorktreeLifecycleState.STATE_MATERIALIZED:
                interrupted = True
                raise KeyboardInterrupt("simulated runtime stop")
            return original_update(scope, candidate_operation_id, state, **kwargs)

        monkeypatch.setattr(manager.lifecycle, "update_state", interrupt_after_git)
        with pytest.raises(KeyboardInterrupt, match="simulated runtime stop"):
            checkpoints.rewind(request)
        monkeypatch.setattr(manager.lifecycle, "update_state", original_update)
        restarted = CheckpointApplication(
            store,
            store.checkpoint_repository(),
            worktree_manager=manager,
            retention=WorktreeRetentionService(store.database, manager),
        )

        result = restarted.rewind(request)
        replay = restarted.rewind(request)

        assert replay == result
        assert (repository / "README.md").read_text(encoding="utf-8") == (
            "checkpoint\n"
        )
        lifecycle = manager.lifecycle.read("checkpoint/rewind", operation_id)
        assert lifecycle is not None and lifecycle.state.value == "completed"
        assert store.connection.execute(
            "SELECT COUNT(*) FROM checkpoint_actions WHERE action = 'rewind'"
        ).fetchone()[0] == 1
    finally:
        store.close()
