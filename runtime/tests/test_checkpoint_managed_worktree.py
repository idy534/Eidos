from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from uuid import uuid4

import pytest

from eidos_runtime.application.checkpoints import CheckpointApplication
from eidos_runtime.application.errors import (
    ApplicationError,
    ApplicationInvalidParamsError,
)
from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.db.errors import StorageError
from eidos_runtime.git import WorktreeManager
from eidos_runtime.domain.worktree import (
    WorktreeLifecycleOperation,
    WorktreeLifecycleScope,
    WorktreeLifecycleState,
)
from eidos_runtime.application.worktree_retention import WorktreeRetentionService
from eidos_runtime.persistence.errors import PersistenceCorruptionError
from eidos_runtime.protocol.methods import (
    CheckpointCreateRequestDto,
    CheckpointForkRequestDto,
    CheckpointRewindRequestDto,
    SessionCreateRequestDto,
)


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q", "-b", "main")
    _git(repository, "config", "user.email", "eidos-tests@example.com")
    _git(repository, "config", "user.name", "Eidos Tests")
    (repository / "README.md").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-qm", "initial")
    return repository


def _setup(
    tmp_path: Path,
) -> tuple[SessionStore, WorktreeManager, SessionApplication, CheckpointApplication]:
    store = SessionStore(tmp_path / "data")
    store.initialize()
    manager = WorktreeManager(
        store.database,
        managed_root=tmp_path / "managed-worktrees",
    )
    sessions = SessionApplication(
        store,
        scan_text=lambda value: value,
        worktree_manager=manager,
    )
    retention = WorktreeRetentionService(store.database, manager)
    checkpoints = CheckpointApplication(
        store,
        store.checkpoint_repository(),
        worktree_manager=manager,
        retention=retention,
    )
    return store, manager, sessions, checkpoints


def _create_session(sessions: SessionApplication, repository: Path) -> dict[str, object]:
    return sessions.create(
        SessionCreateRequestDto(
            workspaceRoot=str(repository), executionMode="worktree"
        )
    ).root


def _identity_hash(*, path: str, device: int, inode: int, owner: int) -> str:
    payload = {"root": path, "device": device, "inode": inode, "owner": owner}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_managed_checkpoint_hash_uses_frozen_worktree_execution_identity(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, manager, sessions, checkpoints = _setup(tmp_path)
    try:
        session = _create_session(sessions, repository)
        run, _item = store.enqueue_run(str(session["id"]), "inspect")
        run_id = str(run["id"])
        snapshot = store.read_run_resolution_snapshot(run_id)

        checkpoint = checkpoints.create(
            CheckpointCreateRequestDto(runId=run_id)
        ).checkpoint
        expected = _identity_hash(
            path=snapshot.workspace_identity.path,
            device=snapshot.workspace_identity.device,
            inode=snapshot.workspace_identity.inode,
            owner=snapshot.workspace_identity.owner,
        )
        repository_metadata = repository.resolve().stat()
        repository_hash = _identity_hash(
            path=str(repository.resolve()),
            device=repository_metadata.st_dev,
            inode=repository_metadata.st_ino,
            owner=repository_metadata.st_uid,
        )

        assert checkpoint.workspace_identity_hash == expected
        assert checkpoint.workspace_identity_hash != repository_hash
        assert checkpoint.git_head == _git(Path(session["worktree"]["worktreeRoot"]), "rev-parse", "HEAD")
    finally:
        store.close()


def test_checkpoint_compatibility_detects_replaced_managed_worktree(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, _manager, sessions, checkpoints = _setup(tmp_path)
    try:
        session = _create_session(sessions, repository)
        run, _item = store.enqueue_run(str(session["id"]), "inspect")
        checkpoint = checkpoints.create(
            CheckpointCreateRequestDto(runId=str(run["id"]))
        ).checkpoint
        worktree_root = Path(session["worktree"]["worktreeRoot"])

        assert store.checkpoint_repository().workspace_is_compatible(checkpoint)
        moved = worktree_root.with_name(f"{worktree_root.name}-moved")
        worktree_root.rename(moved)
        worktree_root.mkdir()

        assert not store.checkpoint_repository().workspace_is_compatible(checkpoint)
    finally:
        store.close()


def test_checkpoint_create_never_falls_back_from_a_corrupt_run_snapshot(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, _manager, sessions, _checkpoints = _setup(tmp_path)
    try:
        session = _create_session(sessions, repository)
        run, _item = store.enqueue_run(str(session["id"]), "inspect")
        store.connection.execute(
            "UPDATE run_resolution_snapshots SET snapshot_json = '{}' WHERE run_id = ?",
            (run["id"],),
        )

        with pytest.raises(PersistenceCorruptionError):
            store.checkpoint_repository().create(str(run["id"]))
    finally:
        store.close()


def test_managed_checkpoint_fork_uses_checkpoint_head_and_replays_once(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, manager, sessions, checkpoints = _setup(tmp_path)
    try:
        parent = _create_session(sessions, repository)
        parent_root = Path(parent["worktree"]["worktreeRoot"])
        (parent_root / "README.md").write_text("checkpoint\n", encoding="utf-8")
        _git(parent_root, "add", "README.md")
        _git(parent_root, "commit", "-qm", "checkpoint state")
        run, _item = store.enqueue_run(str(parent["id"]), "continue work")
        checkpoint = checkpoints.create(
            CheckpointCreateRequestDto(runId=str(run["id"]))
        ).checkpoint

        (parent_root / "README.md").write_text("parent later\n", encoding="utf-8")
        _git(parent_root, "add", "README.md")
        _git(parent_root, "commit", "-qm", "parent later")
        operation_id = str(uuid4())
        request = CheckpointForkRequestDto(
            checkpointId=checkpoint.id,
            operationId=operation_id,
        )
        first = checkpoints.fork(request)
        replay = checkpoints.fork(request)

        parent_projection = store.typed_runtime_repository().read_session_projection(
            str(parent["id"])
        )
        fork_projection = store.typed_runtime_repository().read_session_projection(
            first.run.session_id
        )
        assert parent_projection is not None and parent_projection.worktree is not None
        assert fork_projection is not None and fork_projection.worktree is not None
        assert first == replay
        assert fork_projection.worktree.project_id == parent_projection.worktree.project_id
        assert fork_projection.session.id != parent_projection.session.id
        assert fork_projection.worktree.worktree_id != parent_projection.worktree.worktree_id
        assert fork_projection.worktree.worktree_root != parent_projection.worktree.worktree_root
        assert fork_projection.worktree.branch is None
        assert manager.status(fork_projection.worktree.worktree_id).head == checkpoint.git_head
        assert manager.status(parent_projection.worktree.worktree_id).head != checkpoint.git_head

        fork_root = Path(fork_projection.worktree.worktree_root)
        assert (fork_root / "README.md").read_text(encoding="utf-8") == "checkpoint\n"
        (fork_root / "FORK_ONLY.txt").write_text("fork\n", encoding="utf-8")
        assert not (parent_root / "FORK_ONLY.txt").exists()
        assert store.connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 2
        assert store.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 2
        assert store.connection.execute(
            "SELECT COUNT(*) FROM checkpoint_actions WHERE action = 'fork'"
        ).fetchone()[0] == 1
    finally:
        store.close()


def test_managed_checkpoint_fork_restores_exact_git_workspace_state(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, manager, sessions, checkpoints = _setup(tmp_path)
    try:
        parent = _create_session(sessions, repository)
        parent_root = Path(parent["worktree"]["worktreeRoot"])
        (parent_root / "README.md").write_text("staged\n", encoding="utf-8")
        _git(parent_root, "add", "README.md")
        (parent_root / "README.md").write_text("staged\nunstaged\n", encoding="utf-8")
        (parent_root / "untracked.txt").write_text("untracked\n", encoding="utf-8")
        run, _item = store.enqueue_run(str(parent["id"]), "continue work")

        checkpoint = checkpoints.create(
            CheckpointCreateRequestDto(runId=str(run["id"]), operationId=str(uuid4()))
        ).checkpoint
        assert checkpoint.git_snapshot_id is not None

        (parent_root / "README.md").write_text("later\n", encoding="utf-8")
        (parent_root / "untracked.txt").unlink()
        fork = checkpoints.fork(CheckpointForkRequestDto(
            checkpointId=checkpoint.id, operationId=str(uuid4()),
        ))
        projection = store.typed_runtime_repository().read_session_projection(
            fork.run.session_id
        )
        assert projection is not None and projection.worktree is not None
        fork_root = Path(projection.worktree.worktree_root)

        assert _git(fork_root, "show", ":README.md") == "staged"
        assert (fork_root / "README.md").read_text(encoding="utf-8") == "staged\nunstaged\n"
        assert (fork_root / "untracked.txt").read_text(encoding="utf-8") == "untracked\n"
        snapshot = manager.source_snapshot(fork_root, include_local_changes=True)
        stored = checkpoints.snapshot_service.snapshots.read(checkpoint.git_snapshot_id)
        assert stored is not None
        assert snapshot.head == stored.head
        assert snapshot.status.staged_paths == stored.staged_paths
        assert snapshot.status.unstaged_paths == stored.unstaged_paths
        assert snapshot.status.untracked_paths == stored.untracked_paths
    finally:
        store.close()


def test_managed_checkpoint_rewind_restores_exact_git_workspace_state(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, manager, sessions, checkpoints = _setup(tmp_path)
    try:
        parent = _create_session(sessions, repository)
        root = Path(parent["worktree"]["worktreeRoot"])
        (root / "README.md").write_text("staged\n", encoding="utf-8")
        _git(root, "add", "README.md")
        (root / "README.md").write_text("staged\nunstaged\n", encoding="utf-8")
        (root / "untracked.txt").write_text("untracked\n", encoding="utf-8")
        run, _item = store.enqueue_run(str(parent["id"]), "continue work")
        checkpoint = checkpoints.create(CheckpointCreateRequestDto(
            runId=str(run["id"]), operationId=str(uuid4()),
        )).checkpoint

        store.connection.execute(
            "UPDATE runs SET status = 'succeeded' WHERE id = ?", (run["id"],)
        )
        (root / "README.md").write_text("later\n", encoding="utf-8")
        _git(root, "add", "README.md")
        (root / "untracked.txt").unlink()
        (root / "later.txt").write_text("later\n", encoding="utf-8")

        request = CheckpointRewindRequestDto(
            checkpointId=checkpoint.id, operationId=str(uuid4()),
        )
        first = checkpoints.rewind(request)
        replay = checkpoints.rewind(request)

        assert first == replay
        assert _git(root, "show", ":README.md") == "staged"
        assert (root / "README.md").read_text(encoding="utf-8") == "staged\nunstaged\n"
        assert (root / "untracked.txt").read_text(encoding="utf-8") == "untracked\n"
        assert not (root / "later.txt").exists()
        snapshot = checkpoints.snapshot_service.snapshots.read(checkpoint.git_snapshot_id)
        assert snapshot is not None
        current = manager.source_snapshot(root, include_local_changes=True)
        assert current.fingerprint == snapshot.source_fingerprint
        assert store.connection.execute(
            "SELECT COUNT(*) FROM checkpoint_actions WHERE action = 'rewind'"
        ).fetchone()[0] == 1
    finally:
        store.close()


def test_managed_checkpoint_create_replays_same_snapshot(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    store, _manager, sessions, checkpoints = _setup(tmp_path)
    try:
        parent = _create_session(sessions, repository)
        root = Path(parent["worktree"]["worktreeRoot"])
        (root / "README.md").write_text("checkpoint\n", encoding="utf-8")
        run, _item = store.enqueue_run(str(parent["id"]), "continue work")
        request = CheckpointCreateRequestDto(
            runId=str(run["id"]), operationId=str(uuid4()),
        )

        first = checkpoints.create(request)
        (root / "README.md").write_text("later\n", encoding="utf-8")
        replay = checkpoints.create(request)

        assert replay == first
        assert store.connection.execute(
            "SELECT COUNT(*) FROM checkpoints WHERE run_id = ?", (run["id"],)
        ).fetchone()[0] == 1
        assert store.connection.execute(
            "SELECT COUNT(*) FROM worktree_snapshots WHERE worktree_id = ?",
            (parent["worktree"]["worktreeId"],),
        ).fetchone()[0] == 1
    finally:
        store.close()


def test_managed_checkpoint_rewind_rejects_active_run_before_lifecycle_prepare(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, _manager, sessions, checkpoints = _setup(tmp_path)
    try:
        parent = _create_session(sessions, repository)
        run, _item = store.enqueue_run(str(parent["id"]), "continue work")
        checkpoint = checkpoints.create(CheckpointCreateRequestDto(
            runId=str(run["id"]), operationId=str(uuid4()),
        )).checkpoint

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


def test_checkpoint_snapshot_survives_retention_snapshot_replacement(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, manager, sessions, checkpoints = _setup(tmp_path)
    try:
        parent = _create_session(sessions, repository)
        run, _item = store.enqueue_run(str(parent["id"]), "continue work")
        checkpoint = checkpoints.create(CheckpointCreateRequestDto(
            runId=str(run["id"]), operationId=str(uuid4()),
        )).checkpoint
        assert checkpoint.git_snapshot_id is not None
        checkpoint_snapshot = checkpoints.snapshot_service.snapshots.read(
            checkpoint.git_snapshot_id
        )
        assert checkpoint_snapshot is not None

        worktree = manager.read_worktree(str(parent["worktree"]["worktreeId"]))
        checkpoints.snapshot_service.save(worktree, "retention-new")

        assert checkpoints.snapshot_service.snapshots.read(
            checkpoint.git_snapshot_id
        ) == checkpoint_snapshot
        checkpoints.snapshot_service.verify(checkpoint_snapshot)
    finally:
        store.close()


@pytest.mark.parametrize("action", ["fork", "rewind"])
@pytest.mark.parametrize("damage", ["missing", "checksum", "hidden_ref"])
def test_checkpoint_git_artifact_failure_is_stable_and_has_no_side_effect(
    tmp_path: Path,
    action: str,
    damage: str,
) -> None:
    repository = _repository(tmp_path)
    store, manager, sessions, checkpoints = _setup(tmp_path)
    try:
        parent = _create_session(sessions, repository)
        run, _item = store.enqueue_run(str(parent["id"]), "continue work")
        checkpoint = checkpoints.create(CheckpointCreateRequestDto(
            runId=str(run["id"]), operationId=str(uuid4()),
        )).checkpoint
        snapshot = checkpoints.snapshot_service.snapshots.read(
            checkpoint.git_snapshot_id or ""
        )
        assert snapshot is not None
        if damage == "missing":
            checkpoints.snapshot_service.artifacts.delete(snapshot.artifact_path)
        elif damage == "checksum":
            (Path(snapshot.artifact_path) / "full.patch.gz").write_bytes(b"corrupt")
        else:
            assert manager.git.delete_snapshot_anchor_if_equals(
                repository, snapshot.id, snapshot.head
            )
        store.connection.execute(
            "UPDATE runs SET status = 'succeeded' WHERE id = ?", (run["id"],)
        )
        before = tuple(
            store.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("sessions", "worktrees", "runs", "checkpoint_actions")
        )

        with pytest.raises(ApplicationError) as unavailable:
            if action == "fork":
                checkpoints.fork(CheckpointForkRequestDto(
                    checkpointId=checkpoint.id, operationId=str(uuid4()),
                ))
            else:
                checkpoints.rewind(CheckpointRewindRequestDto(
                    checkpointId=checkpoint.id, operationId=str(uuid4()),
                ))

        assert unavailable.value.code == "CHECKPOINT_GIT_STATE_UNAVAILABLE"
        assert tuple(
            store.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("sessions", "worktrees", "runs", "checkpoint_actions")
        ) == before
    finally:
        store.close()


def test_managed_checkpoint_rewind_resumes_after_runtime_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path)
    store, manager, sessions, checkpoints = _setup(tmp_path)
    try:
        parent = _create_session(sessions, repository)
        root = Path(parent["worktree"]["worktreeRoot"])
        (root / "README.md").write_text("checkpoint\n", encoding="utf-8")
        (root / "untracked.txt").write_text("checkpoint\n", encoding="utf-8")
        run, _item = store.enqueue_run(str(parent["id"]), "continue work")
        checkpoint = checkpoints.create(CheckpointCreateRequestDto(
            runId=str(run["id"]), operationId=str(uuid4()),
        )).checkpoint
        store.connection.execute(
            "UPDATE runs SET status = 'succeeded' WHERE id = ?", (run["id"],)
        )
        (root / "README.md").write_text("later\n", encoding="utf-8")
        (root / "untracked.txt").unlink()
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
            if not interrupted and getattr(state, "value", None) == "state_materialized":
                interrupted = True
                raise KeyboardInterrupt("simulated runtime stop")
            return original_update(scope, candidate_operation_id, state, **kwargs)

        monkeypatch.setattr(manager.lifecycle, "update_state", interrupt_after_git)
        with pytest.raises(KeyboardInterrupt, match="simulated runtime stop"):
            checkpoints.rewind(request)

        lifecycle = manager.lifecycle.read("checkpoint/rewind", operation_id)
        assert lifecycle is not None and lifecycle.state.value == "prepared"
        assert (root / "README.md").read_text(encoding="utf-8") == "checkpoint\n"
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
        assert (root / "README.md").read_text(encoding="utf-8") == "checkpoint\n"
        assert (root / "untracked.txt").read_text(encoding="utf-8") == "checkpoint\n"
        lifecycle = manager.lifecycle.read("checkpoint/rewind", operation_id)
        assert lifecycle is not None and lifecycle.state.value == "completed"
        assert store.connection.execute(
            "SELECT COUNT(*) FROM checkpoint_actions WHERE action = 'rewind'"
        ).fetchone()[0] == 1
    finally:
        store.close()


def test_managed_checkpoint_fork_rejects_caller_path_and_missing_head(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, _manager, sessions, checkpoints = _setup(tmp_path)
    try:
        parent = _create_session(sessions, repository)
        run, _item = store.enqueue_run(str(parent["id"]), "continue work")
        checkpoint = checkpoints.create(
            CheckpointCreateRequestDto(runId=str(run["id"]))
        ).checkpoint
        original_counts = tuple(
            store.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("sessions", "worktrees", "runs", "checkpoint_actions")
        )

        with pytest.raises(ApplicationInvalidParamsError) as path_error:
            checkpoints.fork(CheckpointForkRequestDto(
                checkpointId=checkpoint.id,
                workspaceRoot=str(tmp_path / "caller-selected"),
            ))
        assert path_error.value.code == "MANAGED_CHECKPOINT_FORK_PATH_FORBIDDEN"

        store.connection.execute(
            "UPDATE checkpoints SET git_head = NULL WHERE id = ?", (checkpoint.id,)
        )
        without_head = store.checkpoint_repository().read(checkpoint.id)
        assert without_head is not None
        with pytest.raises(ApplicationError) as head_error:
            checkpoints.fork(CheckpointForkRequestDto(checkpointId=checkpoint.id))
        assert head_error.value.code == "CHECKPOINT_GIT_STATE_UNAVAILABLE"
        assert tuple(
            store.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("sessions", "worktrees", "runs", "checkpoint_actions")
        ) == original_counts
    finally:
        store.close()


def test_failed_checkpoint_fork_unbinds_session_before_create_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path)
    store, manager, sessions, checkpoints = _setup(tmp_path)
    try:
        parent = _create_session(sessions, repository)
        parent_run, _item = store.enqueue_run(str(parent["id"]), "continue")
        checkpoint = checkpoints.create(
            CheckpointCreateRequestDto(runId=str(parent_run["id"]))
        ).checkpoint

        def fail_fork_run(*_args: object, **_kwargs: object) -> object:
            raise StorageError("injected fork Run failure")

        monkeypatch.setattr(store, "enqueue_run", fail_fork_run)
        with pytest.raises(StorageError, match="injected fork Run failure"):
            checkpoints.fork(
                CheckpointForkRequestDto(checkpointId=checkpoint.id)
            )

        assert store.connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
        worktrees = manager.repository.list_worktrees()
        assert len(worktrees) == 2
        rolled_back = next(
            worktree
            for worktree in worktrees
            if worktree.id != parent["worktree"]["worktreeId"]
        )
        assert rolled_back.state.value == "deleted"
        assert not Path(rolled_back.worktree_root).exists()
        assert _git(repository, "branch", "--list", "eidos/*") == ""
    finally:
        store.close()
