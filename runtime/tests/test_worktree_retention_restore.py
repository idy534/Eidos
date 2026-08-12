from __future__ import annotations

import gzip
from datetime import UTC, datetime
from pathlib import Path
import subprocess

import pytest
from uuid import uuid4

from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.application.worktree_retention import WorktreeRetentionService
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.worktree import (
    WorktreeLifecycleOperation,
    WorktreeLifecycleScope,
    WorktreeLifecycleState,
    WorktreeState,
)
from eidos_runtime.domain.worktree_snapshot import WorktreeSnapshotState
from eidos_runtime.git.backend import DulwichGitBackend
from eidos_runtime.git.errors import WorktreeError
from eidos_runtime.git.manager import WorktreeManager
from eidos_runtime.git.models import GitWorkingTreePatch
from eidos_runtime.protocol.methods import (
    SessionCreateRequestDto,
    SessionDeleteRequestDto,
    SessionListRequestDto,
    SessionRestoreWorktreeRequestDto,
)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["/usr/bin/git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.rstrip("\n")


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main", "-q")
    _git(root, "config", "user.email", "eidos@example.com")
    _git(root, "config", "user.name", "Eidos Tests")
    (root / ".gitignore").write_text(".env\nnode_modules/\n", encoding="utf-8")
    (root / "tracked.txt").write_text("base\n", encoding="utf-8")
    (root / "staged.txt").write_text("base\n", encoding="utf-8")
    (root / "deleted.txt").write_text("base\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "base")
    return root


def _runtime(tmp_path: Path) -> tuple[SessionStore, WorktreeManager, Path]:
    store = SessionStore(tmp_path / "data")
    store.initialize()
    return store, WorktreeManager(
        store.database, managed_root=tmp_path / "managed"
    ), _repository(tmp_path)


def test_retention_snapshots_dirty_worktree_and_restores_same_identity(
    tmp_path: Path,
) -> None:
    store, manager, repository = _runtime(tmp_path)
    first = manager.create(repository)
    second = manager.create(repository)
    (repository / ".worktreeinclude").write_text(".env\n", encoding="utf-8")
    (repository / ".env").write_text("SOURCE_SECRET=materialize\n", encoding="utf-8")
    manager.repository.touch_last_used(first.id, at_ms=1)
    manager.repository.touch_last_used(second.id, at_ms=2)

    first_root = Path(first.worktree_root)
    (first_root / "tracked.txt").write_text("unstaged\n", encoding="utf-8")
    (first_root / "staged.txt").write_text("staged\n", encoding="utf-8")
    _git(first_root, "add", "staged.txt")
    (first_root / "deleted.txt").unlink()
    (first_root / "untracked.bin").write_bytes(b"\x00\x01binary\n")
    (first_root / ".env").write_text("SECRET=must-not-be-snapshotted\n")
    (first_root / "node_modules").mkdir()
    (first_root / "node_modules" / "cache").write_text("ignored")

    service = WorktreeRetentionService(store.database, manager)
    service.settings.update(automatic_cleanup=True, managed_worktree_limit=1)
    report = service.reconcile()

    assert report.cleaned_worktree_ids == (first.id,)
    assert manager.read_worktree(first.id).state is WorktreeState.DELETED
    snapshot = service.snapshots.latest_ready(first.id)
    assert snapshot is not None
    assert snapshot.state is WorktreeSnapshotState.READY
    lifecycle = store.connection.execute(
        """
        SELECT snapshot_id, snapshot_head, snapshot_fingerprint
        FROM worktree_lifecycle_operations
        WHERE scope = 'worktree/retention-cleanup' AND worktree_id = ?
        """,
        (first.id,),
    ).fetchone()
    assert lifecycle["snapshot_id"] == snapshot.id
    assert lifecycle["snapshot_head"] == snapshot.head
    assert lifecycle["snapshot_fingerprint"] == snapshot.source_fingerprint
    assert not Path(first.worktree_root).exists()
    artifact_bytes = b"".join(
        gzip.decompress((Path(snapshot.artifact_path) / name).read_bytes())
        for name in ("full.patch.gz", "staged.patch.gz")
    )
    assert b"SECRET=must-not-be-snapshotted" not in artifact_bytes
    assert b"node_modules" not in artifact_bytes

    restored = service.restore_worktree(first.id)
    assert restored.id == first.id
    assert restored.state is WorktreeState.ACTIVE
    assert manager.head(Path(restored.worktree_root)) == snapshot.head
    assert (Path(restored.worktree_root) / "tracked.txt").read_text() == "unstaged\n"
    assert (Path(restored.worktree_root) / "staged.txt").read_text() == "staged\n"
    assert (Path(restored.worktree_root) / ".env").read_text() == "SOURCE_SECRET=materialize\n"
    assert not (Path(restored.worktree_root) / "node_modules").exists()
    assert set(_git(Path(restored.worktree_root), "status", "--short").splitlines()) == {
        " M tracked.txt",
        "M  staged.txt",
        " D deleted.txt",
        "?? untracked.bin",
    }
    assert service.snapshots.read(snapshot.id).state is WorktreeSnapshotState.RESTORED
    assert service.manager.git.snapshot_anchor(
        repository, snapshot.id
    ) is None


def test_retention_limit_fifteen_cleans_two_oldest_eligible_worktrees(
    tmp_path: Path,
) -> None:
    store, manager, repository = _runtime(tmp_path)
    worktrees = [manager.create(repository) for _ in range(17)]
    for index, worktree in enumerate(worktrees, start=1):
        manager.repository.touch_last_used(worktree.id, at_ms=index)

    service = WorktreeRetentionService(store.database, manager)
    service.settings.update(automatic_cleanup=True, managed_worktree_limit=15)
    report = service.reconcile()

    assert report.cleaned_worktree_ids == (worktrees[0].id, worktrees[1].id)
    assert sum(
        manager.read_worktree(worktree.id).state is WorktreeState.ACTIVE
        for worktree in worktrees
    ) == 15
    assert all(
        service.snapshots.latest_ready(worktree.id) is not None
        for worktree in worktrees[:2]
    )


def test_automatic_cleanup_setting_disables_retention_pass(tmp_path: Path) -> None:
    store, manager, repository = _runtime(tmp_path)
    first = manager.create(repository)
    second = manager.create(repository)
    manager.repository.touch_last_used(first.id, at_ms=1)
    manager.repository.touch_last_used(second.id, at_ms=2)
    service = WorktreeRetentionService(store.database, manager)
    service.settings.update(automatic_cleanup=False, managed_worktree_limit=1)

    report = service.reconcile()

    assert report.cleaned_worktree_ids == ()
    assert manager.read_worktree(first.id).state is WorktreeState.ACTIVE
    assert service.snapshots.latest_ready(first.id) is None


def test_storage_reconciliation_retains_unproven_orphan_candidates(
    tmp_path: Path,
) -> None:
    store, manager, repository = _runtime(tmp_path)
    manager.create(repository)
    service = WorktreeRetentionService(store.database, manager)
    head = manager.head(repository)
    orphan = service.artifacts.write(
        "orphan",
        GitWorkingTreePatch(full_patch=b"", staged_patch=b""),
    )
    manager.git.create_snapshot_anchor(repository, "orphan", head)

    service.reconcile_storage()

    assert orphan.path.exists()
    assert manager.git.snapshot_anchor(repository, "orphan") == head


def test_retention_preserves_detached_head_with_hidden_anchor(tmp_path: Path) -> None:
    store, manager, repository = _runtime(tmp_path)
    first = manager.create(repository)
    second = manager.create(repository)
    manager.repository.touch_last_used(first.id, at_ms=1)
    manager.repository.touch_last_used(second.id, at_ms=2)
    root = Path(first.worktree_root)
    (root / "detached.txt").write_text("commit\n", encoding="utf-8")
    _git(root, "add", "detached.txt")
    _git(root, "commit", "-qm", "detached")
    detached_head = _git(root, "rev-parse", "HEAD")
    assert _git(root, "branch", "--show-current") == ""

    service = WorktreeRetentionService(store.database, manager)
    service.settings.update(automatic_cleanup=True, managed_worktree_limit=1)
    service.reconcile()
    snapshot = service.snapshots.latest_ready(first.id)
    assert snapshot is not None
    assert snapshot.head == detached_head
    assert service.manager.git.snapshot_anchor(repository, snapshot.id) == detached_head
    assert _git(repository, "branch", "--list", "--contains", detached_head) == ""

    restored = service.restore_worktree(first.id)
    assert restored.id == first.id
    assert manager.head(Path(restored.worktree_root)) == detached_head


def test_restore_materializes_ignored_source_files_for_clean_snapshot(
    tmp_path: Path,
) -> None:
    store, manager, repository = _runtime(tmp_path)
    first = manager.create(repository)
    second = manager.create(repository)
    (repository / ".worktreeinclude").write_text(".env\n", encoding="utf-8")
    (repository / ".env").write_text("SOURCE_SECRET=clean\n", encoding="utf-8")
    manager.repository.touch_last_used(first.id, at_ms=1)
    manager.repository.touch_last_used(second.id, at_ms=2)

    service = WorktreeRetentionService(store.database, manager)
    service.settings.update(automatic_cleanup=True, managed_worktree_limit=1)
    service.reconcile()
    assert not (Path(first.worktree_root) / ".env").exists()

    service.restore_worktree(first.id)
    assert (Path(first.worktree_root) / ".env").read_text() == "SOURCE_SECRET=clean\n"


def test_retention_reconciles_after_worktree_remove_before_lifecycle_update(
    tmp_path: Path,
) -> None:
    store, manager, repository = _runtime(tmp_path)
    first = manager.create(repository)
    second = manager.create(repository)
    manager.repository.touch_last_used(first.id, at_ms=1)
    manager.repository.touch_last_used(second.id, at_ms=2)
    service = WorktreeRetentionService(store.database, manager)
    service.settings.update(automatic_cleanup=True, managed_worktree_limit=1)

    operation = service._find_or_prepare_cleanup(first)
    service._save_snapshot(first, operation.snapshot_id or "")
    operation = manager.lifecycle.update_state(
        operation.scope,
        operation.operation_id,
        WorktreeLifecycleState.SNAPSHOT_SAVED,
    )
    manager.clean_for_retention(first.id)
    assert manager.read_worktree(first.id).state is WorktreeState.DELETED

    WorktreeRetentionService(store.database, manager).reconcile_operations()
    recovered = manager.lifecycle.read(operation.scope, operation.operation_id)
    assert recovered is not None
    assert recovered.state is WorktreeLifecycleState.COMPLETED


def test_restore_reconciles_after_worktree_add_before_completion(
    tmp_path: Path,
) -> None:
    store, manager, repository = _runtime(tmp_path)
    first = manager.create(repository)
    second = manager.create(repository)
    manager.repository.touch_last_used(first.id, at_ms=1)
    manager.repository.touch_last_used(second.id, at_ms=2)
    service = WorktreeRetentionService(store.database, manager)
    service.settings.update(automatic_cleanup=True, managed_worktree_limit=1)
    service.reconcile()
    snapshot = service.snapshots.latest_ready(first.id)
    assert snapshot is not None

    now = datetime.now(UTC).replace(microsecond=0)
    operation = manager.lifecycle.prepare(
        WorktreeLifecycleOperation(
            scope=WorktreeLifecycleScope.RESTORE,
            operation_id="restore-crash",
            state=WorktreeLifecycleState.PREPARED,
            project_id=snapshot.project_id,
            repository_root=str(repository),
            worktree_id=first.id,
            worktree_root=first.worktree_root,
            base_ref=snapshot.base_ref,
            base_commit=snapshot.base_commit,
            session_id=snapshot.session_id,
            snapshot_id=snapshot.id,
            snapshot_head=snapshot.head,
            snapshot_fingerprint=snapshot.source_fingerprint,
            created_at=now,
            updated_at=now,
        )
    )
    manager.restore_worktree(
        first.id,
        head=snapshot.head,
        changes=service.artifacts.read(snapshot.artifact_path),
        expected_fingerprint=snapshot.source_fingerprint,
    )
    operation = manager.lifecycle.update_state(
        operation.scope,
        operation.operation_id,
        WorktreeLifecycleState.WORKTREE_CREATED,
    )

    WorktreeRetentionService(store.database, manager).reconcile_operations()
    recovered = manager.lifecycle.read(operation.scope, operation.operation_id)
    assert recovered is not None
    assert recovered.state is WorktreeLifecycleState.COMPLETED
    assert manager.read_worktree(first.id).state is WorktreeState.ACTIVE
    assert service.snapshots.read(snapshot.id).state is WorktreeSnapshotState.RESTORED


def test_restore_reconciles_after_anchor_delete_before_snapshot_mark(
    tmp_path: Path,
) -> None:
    store, manager, repository = _runtime(tmp_path)
    first = manager.create(repository)
    second = manager.create(repository)
    manager.repository.touch_last_used(first.id, at_ms=1)
    manager.repository.touch_last_used(second.id, at_ms=2)
    service = WorktreeRetentionService(store.database, manager)
    service.settings.update(automatic_cleanup=True, managed_worktree_limit=1)
    service.reconcile()
    snapshot = service.snapshots.latest_ready(first.id)
    assert snapshot is not None

    now = datetime.now(UTC).replace(microsecond=0)
    operation = manager.lifecycle.prepare(
        WorktreeLifecycleOperation(
            scope=WorktreeLifecycleScope.RESTORE,
            operation_id="restore-anchor-crash",
            state=WorktreeLifecycleState.PREPARED,
            project_id=snapshot.project_id,
            repository_root=str(repository),
            worktree_id=first.id,
            worktree_root=first.worktree_root,
            base_ref=snapshot.base_ref,
            base_commit=snapshot.base_commit,
            session_id=snapshot.session_id,
            snapshot_id=snapshot.id,
            snapshot_head=snapshot.head,
            snapshot_fingerprint=snapshot.source_fingerprint,
            created_at=now,
            updated_at=now,
        )
    )
    manager.restore_worktree(
        first.id,
        head=snapshot.head,
        changes=service.artifacts.read(snapshot.artifact_path),
        expected_fingerprint=snapshot.source_fingerprint,
    )
    for state in (
        WorktreeLifecycleState.WORKTREE_CREATED,
        WorktreeLifecycleState.STATE_MATERIALIZED,
        WorktreeLifecycleState.WORKTREE_REBOUND,
    ):
        operation = manager.lifecycle.update_state(
            operation.scope, operation.operation_id, state
        )
    assert manager.git.delete_snapshot_anchor_if_equals(
        repository, snapshot.id, snapshot.head
    ) is True

    WorktreeRetentionService(store.database, manager).reconcile_operations()

    recovered = manager.lifecycle.read(operation.scope, operation.operation_id)
    assert recovered is not None
    assert recovered.state is WorktreeLifecycleState.COMPLETED
    assert service.snapshots.read(snapshot.id).state is WorktreeSnapshotState.RESTORED
    assert not Path(snapshot.artifact_path).exists()


def test_startup_housekeeping_removes_restored_snapshot_anchor(
    tmp_path: Path,
) -> None:
    store, manager, repository = _runtime(tmp_path)
    first = manager.create(repository)
    second = manager.create(repository)
    manager.repository.touch_last_used(first.id, at_ms=1)
    manager.repository.touch_last_used(second.id, at_ms=2)
    service = WorktreeRetentionService(store.database, manager)
    service.settings.update(automatic_cleanup=True, managed_worktree_limit=1)
    service.reconcile()
    snapshot = service.snapshots.latest_ready(first.id)
    assert snapshot is not None

    # Recreate the crash window: metadata is restored, but ref cleanup was not
    # persisted yet. Startup housekeeping must use compare-and-delete.
    service.snapshots.mark_restored(snapshot.id)
    manager.git.create_snapshot_anchor(repository, snapshot.id, snapshot.head)
    assert manager.git.snapshot_anchor(repository, snapshot.id) == snapshot.head

    WorktreeRetentionService(store.database, manager).reconcile_storage()

    assert manager.git.snapshot_anchor(repository, snapshot.id) is None
    assert not Path(snapshot.artifact_path).exists()


def test_restore_keeps_snapshot_when_artifact_checksum_fails(tmp_path: Path) -> None:
    store, manager, repository = _runtime(tmp_path)
    first = manager.create(repository)
    second = manager.create(repository)
    manager.repository.touch_last_used(first.id, at_ms=1)
    manager.repository.touch_last_used(second.id, at_ms=2)
    service = WorktreeRetentionService(store.database, manager)
    service.settings.update(automatic_cleanup=True, managed_worktree_limit=1)
    service.reconcile()
    snapshot = service.snapshots.latest_ready(first.id)
    assert snapshot is not None
    artifact = Path(snapshot.artifact_path) / "full.patch.gz"
    artifact.write_bytes(artifact.read_bytes() + b"tampered")

    with pytest.raises(WorktreeError):
        service.restore_worktree(first.id)

    assert manager.read_worktree(first.id).state is WorktreeState.DELETED
    assert service.snapshots.read(snapshot.id).state is WorktreeSnapshotState.READY
    assert artifact.exists()


def test_session_delete_snapshot_cleanup_recovers_durably(
    tmp_path: Path,
) -> None:
    store, manager, repository = _runtime(tmp_path)
    service = WorktreeRetentionService(store.database, manager)
    application = SessionApplication(
        store,
        scan_text=lambda value: value,
        worktree_manager=manager,
        retention=service,
    )
    created = application.create(
        SessionCreateRequestDto(workspaceRoot=str(repository), executionMode="worktree")
    )
    worktree_id = created.worktree.worktree_id if created.worktree is not None else ""
    assert worktree_id
    service.cleanup_worktree(worktree_id, reason="retention")
    snapshot = service.snapshots.latest_ready(worktree_id)
    assert snapshot is not None
    worktree = manager.read_worktree(worktree_id)
    now = datetime.now(UTC).replace(microsecond=0)
    session_delete_operation_id = str(uuid4())
    operation = manager.lifecycle.prepare(
        WorktreeLifecycleOperation(
            scope=WorktreeLifecycleScope.SESSION_DELETE,
            operation_id=session_delete_operation_id,
            state=WorktreeLifecycleState.WORKTREE_DELETED,
            project_id=worktree.project_id,
            repository_root=str(repository),
            worktree_id=worktree.id,
            worktree_root=worktree.worktree_root,
            base_ref=worktree.base_ref,
            base_commit=worktree.base_commit,
            session_id=created.id,
            snapshot_id=snapshot.id,
            snapshot_head=snapshot.head,
            snapshot_fingerprint=snapshot.source_fingerprint,
            created_at=now,
            updated_at=now,
        )
    )

    WorktreeRetentionService(store.database, manager).reconcile_operations()
    assert service.snapshots.list_for_worktree(worktree_id) == ()
    assert store.typed_runtime_repository().read_session(created.id) is not None
    recovered = manager.lifecycle.read(operation.scope, operation.operation_id)
    assert recovered is not None
    assert recovered.state is WorktreeLifecycleState.WORKTREE_DELETED

    deleted = application.delete(
        SessionDeleteRequestDto(
            sessionId=created.id,
            operationId=operation.operation_id,
        )
    )
    assert deleted.deleted_session_id == created.id
    assert manager.lifecycle.read(operation.scope, operation.operation_id).state is WorktreeLifecycleState.COMPLETED


def test_retention_uses_last_used_and_skips_protected_worktree(tmp_path: Path) -> None:
    store, manager, repository = _runtime(tmp_path)
    first = manager.create(repository)
    second = manager.create(repository)
    manager.repository.touch_last_used(first.id, at_ms=1)
    manager.repository.touch_last_used(second.id, at_ms=2)
    session = store.typed_runtime_repository().create_session(
        str(repository),
        worktree_id=first.id,
        execution_mode="worktree",
        project_id=first.project_id,
    ).value
    with store.database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO runs (
                id, session_id, user_input, model_id, model_profile_json,
                status, created_at, updated_at
            ) VALUES ('active-run', ?, 'input', 'model', '{}', 'running', 1, 1)
            """,
            (session.id,),
        )

    service = WorktreeRetentionService(store.database, manager)
    service.settings.update(automatic_cleanup=True, managed_worktree_limit=1)
    report = service.reconcile()

    assert report.cleaned_worktree_ids == ()
    assert report.skipped == (
        report.skipped[0],
    )
    assert report.skipped[0].worktree_id == first.id
    assert report.skipped[0].reason == "active_run"
    assert manager.read_worktree(first.id).state is WorktreeState.ACTIVE


@pytest.mark.parametrize("protected_kind", [
    "invalid",
    "cleanup_required",
    "unfinished_lifecycle",
])
def test_retention_skips_invalid_and_unfinished_lifecycle_worktrees(
    tmp_path: Path,
    protected_kind: str,
) -> None:
    store, manager, repository = _runtime(tmp_path)
    first = manager.create(repository)
    second = manager.create(repository)
    manager.repository.touch_last_used(first.id, at_ms=1)
    manager.repository.touch_last_used(second.id, at_ms=2)
    if protected_kind == "invalid":
        manager.repository.update_state(first.id, WorktreeState.INVALID)
    else:
        now = datetime.now(UTC).replace(microsecond=0)
        manager.lifecycle.prepare(
            WorktreeLifecycleOperation(
                scope=(
                    WorktreeLifecycleScope.RETENTION_CLEANUP
                    if protected_kind == "cleanup_required"
                    else WorktreeLifecycleScope.ATTACH_BRANCH
                ),
                operation_id=f"protected-{protected_kind}",
                state=(
                    WorktreeLifecycleState.CLEANUP_REQUIRED
                    if protected_kind == "cleanup_required"
                    else WorktreeLifecycleState.PREPARED
                ),
                project_id=first.project_id,
                repository_root=str(repository),
                worktree_id=first.id,
                worktree_root=first.worktree_root,
                created_at=now,
                updated_at=now,
            )
        )

    service = WorktreeRetentionService(store.database, manager)
    service.settings.update(automatic_cleanup=True, managed_worktree_limit=1)
    report = service.reconcile()

    assert report.cleaned_worktree_ids == ()
    if protected_kind != "invalid":
        assert report.skipped[0].worktree_id == first.id
    assert manager.read_worktree(first.id).state is not WorktreeState.DELETED


def test_retention_skips_unfinished_handoff(tmp_path: Path) -> None:
    store, manager, repository = _runtime(tmp_path)
    first = manager.create(repository)
    second = manager.create(repository)
    manager.repository.touch_last_used(first.id, at_ms=1)
    manager.repository.touch_last_used(second.id, at_ms=2)
    session = store.typed_runtime_repository().create_session(
        str(repository),
        worktree_id=first.id,
        execution_mode="worktree",
        project_id=first.project_id,
    ).value
    project = manager.project(first.project_id)
    assert project.git_common_dir is not None
    source = manager.source_snapshot(repository, include_local_changes=False)
    target = manager.source_snapshot(
        Path(first.worktree_root), include_local_changes=False
    )
    with store.database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO session_handoff_operations (
                scope, operation_id, state, session_id, project_id,
                source_mode, target_mode, source_root, target_root,
                source_common_dir, target_common_dir, associated_worktree_id,
                target_worktree_new, target_base_ref, target_base_commit,
                source_head, source_branch, source_dirty, source_fingerprint,
                target_head, target_branch, target_dirty, target_fingerprint,
                created_at, updated_at
            ) VALUES (
                'session/handoff-worktree', 'unfinished-handoff', 'prepared', ?, ?,
                'local', 'worktree', ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, 0, ?, ?, ?, 0, ?, 1, 1
            )
            """,
            (
                session.id,
                first.project_id,
                str(repository),
                first.worktree_root,
                project.git_common_dir,
                project.git_common_dir,
                first.id,
                first.base_ref,
                first.base_commit,
                source.head,
                source.branch,
                source.fingerprint,
                target.head,
                target.branch,
                target.fingerprint,
            ),
        )

    service = WorktreeRetentionService(store.database, manager)
    service.settings.update(automatic_cleanup=True, managed_worktree_limit=1)
    report = service.reconcile()

    assert report.cleaned_worktree_ids == ()
    assert report.skipped[0] == report.skipped[0].__class__(
        worktree_id=first.id,
        reason="unfinished_handoff",
    )
    assert manager.read_worktree(first.id).state is WorktreeState.ACTIVE


def test_hidden_ref_compare_and_delete_does_not_delete_changed_ref(tmp_path: Path) -> None:
    store, manager, repository = _runtime(tmp_path)
    backend = DulwichGitBackend()
    head = manager.head(repository)
    backend.create_snapshot_anchor(repository, "S1", head)
    other = "0" * len(head)
    assert backend.delete_snapshot_anchor_if_equals(repository, "S1", other) is False
    assert backend.snapshot_anchor(repository, "S1") == head
    assert backend.delete_snapshot_anchor_if_equals(repository, "S1", head) is True
    assert backend.snapshot_anchor(repository, "S1") is None


def test_session_restore_keeps_associated_identity_and_delete_cleans_snapshot(
    tmp_path: Path,
) -> None:
    store, manager, repository = _runtime(tmp_path)
    service = WorktreeRetentionService(store.database, manager)
    application = SessionApplication(
        store,
        scan_text=lambda value: value,
        worktree_manager=manager,
        retention=service,
    )
    created = application.create(
        SessionCreateRequestDto(
            workspaceRoot=str(repository),
            executionMode="worktree",
            operationId=str(uuid4()),
        )
    )
    worktree_id = created.worktree.worktree_id if created.worktree is not None else ""
    assert worktree_id
    service.cleanup_worktree(worktree_id, reason="retention")

    listed = application.list(SessionListRequestDto())
    assert listed.items[0].worktree_restore_available is True
    assert listed.items[0].associated_worktree_id == worktree_id

    restored = application.restore_worktree(
        SessionRestoreWorktreeRequestDto(
            sessionId=created.id,
            operationId=str(uuid4()),
        )
    )
    assert restored.worktree_id == worktree_id
    assert restored.associated_worktree_id == worktree_id
    assert restored.worktree_restore_available is False

    service.cleanup_worktree(worktree_id, reason="retention")
    deleted = application.delete(
        SessionDeleteRequestDto(sessionId=created.id, operationId=str(uuid4()))
    )
    assert deleted.deleted_session_id == created.id
    assert service.snapshots.list_for_worktree(worktree_id) == ()
