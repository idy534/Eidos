from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import logging
from pathlib import Path
import uuid
from collections.abc import Callable

from eidos_runtime.db.database import Database
from eidos_runtime.db.errors import StorageError
from eidos_runtime.domain.worktree import (
    BranchOwnership,
    Worktree,
    WorktreeLifecycleOperation,
    WorktreeLifecycleScope,
    WorktreeLifecycleState,
    WorktreeOwnership,
    WorktreeState,
)
from eidos_runtime.domain.worktree_snapshot import WorktreeSnapshot
from eidos_runtime.git.errors import WorktreeError
from eidos_runtime.git.manager import WorktreeManager
from eidos_runtime.git.models import GitSourceSnapshot
from eidos_runtime.git.snapshot_artifacts import SnapshotArtifactStore
from eidos_runtime.persistence.worktree_snapshots import WorktreeSnapshotRepository
from eidos_runtime.persistence.worktree_settings import WorktreeSettingsRepository


@dataclass(frozen=True)
class RetentionSkipped:
    worktree_id: str
    reason: str


@dataclass(frozen=True)
class RetentionReport:
    cleaned_worktree_ids: tuple[str, ...] = ()
    skipped: tuple[RetentionSkipped, ...] = ()


class WorktreeRetentionService:
    """Owns the bounded retention and snapshot/restore use cases."""

    def __init__(
        self,
        database: Database,
        manager: WorktreeManager,
        *,
        snapshot_repository: WorktreeSnapshotRepository | None = None,
        settings_repository: WorktreeSettingsRepository | None = None,
        artifact_store: SnapshotArtifactStore | None = None,
        id_factory: Callable[[], str] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        if database.health_state != "ready":
            raise WorktreeError("storage_not_ready")
        self.database = database
        self.manager = manager
        self.snapshots = snapshot_repository or WorktreeSnapshotRepository(database)
        self.settings = settings_repository or WorktreeSettingsRepository(database)
        data_directory = database.data_directory
        if data_directory is None:
            raise WorktreeError("storage_not_ready")
        self.artifacts = artifact_store or SnapshotArtifactStore(data_directory)
        self._id_factory = id_factory or (lambda: f"ws_{uuid.uuid4().hex}")
        self.logger = logger or logging.getLogger(__name__)

    def reconcile(self) -> RetentionReport:
        """Resume 3D operations, then perform one bounded cleanup pass."""

        cleaned: list[str] = []
        skipped: list[RetentionSkipped] = []
        self.reconcile_operations()
        self.reconcile_storage()
        settings = self.settings.read()
        if not settings.automatic_cleanup:
            return RetentionReport()
        worktrees = [
            worktree
            for project in self.manager.repository.list_projects()
            for worktree in self.manager.repository.list_worktrees(project.id)
            if worktree.ownership is WorktreeOwnership.MANAGED
            and worktree.state is WorktreeState.ACTIVE
        ]
        ordered = sorted(
            worktrees,
            key=lambda worktree: (worktree.last_used_at, worktree.created_at),
            reverse=True,
        )
        for worktree in reversed(ordered[settings.managed_worktree_limit :]):
            reason = self._protected_reason(worktree)
            if reason is not None:
                skipped.append(RetentionSkipped(worktree.id, reason))
                continue
            try:
                self.cleanup_worktree(worktree.id, reason="retention")
            except (WorktreeError, StorageError, OSError, ValueError) as error:
                skipped.append(
                    RetentionSkipped(
                        worktree.id,
                        error.code if isinstance(error, WorktreeError) else "snapshot_failed",
                    )
                )
                continue
            cleaned.append(worktree.id)
        return RetentionReport(tuple(cleaned), tuple(skipped))

    def cleanup_worktree(self, worktree_id: str, *, reason: str) -> Worktree:
        if reason != "retention":
            raise WorktreeError("worktree_cleanup_reason_invalid")
        worktree = self.manager.read_worktree(worktree_id)
        if worktree.ownership is not WorktreeOwnership.MANAGED:
            raise WorktreeError("worktree_ownership_invalid")
        if worktree.state is WorktreeState.DELETED:
            return worktree
        protected = self._protected_reason(worktree)
        if protected is not None:
            raise WorktreeError(protected)
        operation = self._find_or_prepare_cleanup(worktree)
        return self._resume_cleanup(operation)

    def restore_worktree(
        self, worktree_id: str, *, operation_id: str | None = None
    ) -> Worktree:
        operation = (
            self.manager.lifecycle.read(WorktreeLifecycleScope.RESTORE, operation_id)
            if operation_id is not None
            else None
        )
        snapshot = self.snapshots.latest_ready(worktree_id)
        if snapshot is None and operation is not None and operation.snapshot_id is not None:
            restored_snapshot = self.snapshots.read(operation.snapshot_id)
            if restored_snapshot is not None and restored_snapshot.state.value == "restored":
                worktree = self.manager.read_worktree(worktree_id)
                if worktree.state is WorktreeState.ACTIVE:
                    self._verify_restored_worktree(worktree, restored_snapshot)
                    self._complete_restored_operation(operation, restored_snapshot)
                    if operation.state is WorktreeLifecycleState.WORKTREE_REBOUND:
                        self.manager.lifecycle.update_state(
                            operation.scope,
                            operation.operation_id,
                            WorktreeLifecycleState.COMPLETED,
                        )
                    return worktree
            raise WorktreeError("worktree_restore_required")
        if snapshot is None:
            raise WorktreeError("worktree_restore_required")
        current_worktree = self.manager.read_worktree(worktree_id)
        if operation is None and current_worktree.state is not WorktreeState.DELETED:
            raise WorktreeError("worktree_restore_not_required")
        operation_id = operation_id or f"restore-{worktree_id}-{snapshot.id}"
        if operation is None:
            operation = self.manager.lifecycle.read(
                WorktreeLifecycleScope.RESTORE, operation_id
            )
        if operation is None:
            operation = self.manager.lifecycle.prepare(
                WorktreeLifecycleOperation(
                    scope=WorktreeLifecycleScope.RESTORE,
                    operation_id=operation_id,
                    state=WorktreeLifecycleState.PREPARED,
                    project_id=snapshot.project_id,
                    repository_root=self.manager.project(snapshot.project_id).workspace_root,
                    worktree_id=worktree_id,
                    worktree_root=self.manager.read_worktree(worktree_id).worktree_root,
                    base_ref=snapshot.base_ref,
                    base_commit=snapshot.base_commit,
                    branch=None,
                    session_id=snapshot.session_id,
                    snapshot_id=snapshot.id,
                    snapshot_head=snapshot.head,
                    snapshot_fingerprint=snapshot.source_fingerprint,
                    created_at=_now(),
                    updated_at=_now(),
                )
            )
        if operation.snapshot_id != snapshot.id:
            raise WorktreeError("worktree_restore_conflict")
        if (
            current_worktree.state is WorktreeState.ACTIVE
            and operation.state
            in {
                WorktreeLifecycleState.WORKTREE_REBOUND,
                WorktreeLifecycleState.COMPLETED,
            }
        ):
            if snapshot.state.value == "ready":
                self._verify_snapshot_artifact(snapshot)
            self._verify_restored_worktree(current_worktree, snapshot)
            self._complete_restored_operation(
                operation,
                snapshot,
                allow_missing_anchor=True,
            )
            if operation.state is WorktreeLifecycleState.WORKTREE_REBOUND:
                self.manager.lifecycle.update_state(
                    operation.scope,
                    operation.operation_id,
                    WorktreeLifecycleState.COMPLETED,
                )
            return current_worktree
        if operation.state is WorktreeLifecycleState.COMPLETED:
            if snapshot is not None:
                self._complete_restored_operation(operation, snapshot)
            return self.manager.read_worktree(worktree_id)
        if operation.state is WorktreeLifecycleState.CLEANUP_REQUIRED:
            raise WorktreeError("worktree_restore_required")
        try:
            self._verify_snapshot(snapshot)
            changes = self.artifacts.read(snapshot.artifact_path)
            restored = self.manager.restore_worktree(
                worktree_id,
                head=snapshot.head,
                changes=changes,
                expected_fingerprint=snapshot.source_fingerprint,
            )
            if operation.state is WorktreeLifecycleState.PREPARED:
                operation = self.manager.lifecycle.update_state(
                    operation.scope,
                    operation.operation_id,
                    WorktreeLifecycleState.WORKTREE_CREATED,
                )
            if operation.state is WorktreeLifecycleState.WORKTREE_CREATED:
                operation = self.manager.lifecycle.update_state(
                    operation.scope,
                    operation.operation_id,
                    WorktreeLifecycleState.STATE_MATERIALIZED,
                )
            if operation.state is WorktreeLifecycleState.STATE_MATERIALIZED:
                operation = self.manager.lifecycle.update_state(
                    operation.scope,
                    operation.operation_id,
                    WorktreeLifecycleState.WORKTREE_REBOUND,
                )
            self._complete_restored_operation(operation, snapshot)
            if operation.state is WorktreeLifecycleState.WORKTREE_REBOUND:
                operation = self.manager.lifecycle.update_state(
                    operation.scope,
                    operation.operation_id,
                    WorktreeLifecycleState.COMPLETED,
                )
            return restored
        except Exception as error:
            try:
                self.manager.cleanup_partial_restore(worktree_id, snapshot.head)
            except Exception:
                self.logger.exception(
                    "partial Worktree restore cleanup failed",
                    extra={"worktree_id": worktree_id, "snapshot_id": snapshot.id},
                )
            try:
                self.manager.lifecycle.update_state(
                    operation.scope,
                    operation.operation_id,
                    WorktreeLifecycleState.CLEANUP_REQUIRED,
                    error_code=(
                        error.code if isinstance(error, WorktreeError) else "worktree_restore_failed"
                    ),
                )
            except Exception:
                self.logger.exception("restore lifecycle state could not be persisted")
            if isinstance(error, WorktreeError):
                raise
            raise WorktreeError("worktree_restore_failed") from error

    def _complete_restored_operation(
        self,
        operation: WorktreeLifecycleOperation,
        snapshot: WorktreeSnapshot,
        *,
        allow_missing_anchor: bool = False,
    ) -> None:
        if snapshot.state.value == "ready":
            self._verify_snapshot_artifact(snapshot)
            if self._read_snapshot_anchor(snapshot) is None and not allow_missing_anchor:
                raise WorktreeError("worktree_snapshot_anchor_mismatch")
        if snapshot.state.value != "ready" or self._read_snapshot_anchor(snapshot) is not None:
            self._delete_anchor_if_expected(snapshot)
        if snapshot.state.value == "ready":
            self.snapshots.mark_restored(snapshot.id)
        try:
            self.artifacts.delete(snapshot.artifact_path)
        except OSError:
            self.logger.warning(
                "restored Worktree snapshot artifact cleanup deferred",
                extra={"snapshot_id": snapshot.id},
            )
    def delete_snapshots_for_worktree(self, worktree_id: str) -> None:
        """Delete snapshot metadata and artifacts for explicit Session delete."""

        for snapshot in self.snapshots.list_for_worktree(worktree_id):
            self._delete_anchor_if_expected(snapshot)
            try:
                self.artifacts.delete(snapshot.artifact_path)
            except (OSError, ValueError) as error:
                raise WorktreeError("worktree_snapshot_cleanup_required") from error
            self.snapshots.delete(snapshot.id)

    def has_ready_snapshot(self, worktree_id: str) -> bool:
        return self.snapshots.latest_ready(worktree_id) is not None

    def latest_ready_snapshot_id(self, worktree_id: str) -> str | None:
        snapshot = self.snapshots.latest_ready(worktree_id)
        return snapshot.id if snapshot is not None else None

    def latest_ready_snapshot(self, worktree_id: str) -> WorktreeSnapshot | None:
        return self.snapshots.latest_ready(worktree_id)

    def reconcile_operations(self) -> None:
        for operation in self.manager.lifecycle.list_unfinished():
            if operation.scope is WorktreeLifecycleScope.RETENTION_CLEANUP:
                try:
                    self._resume_cleanup(operation)
                except Exception as error:
                    self._mark_cleanup_required(operation, error)
                    self.logger.exception(
                        "retention cleanup recovery requires manual inspection",
                        extra={"operation_id": operation.operation_id},
                    )
            elif operation.scope is WorktreeLifecycleScope.RESTORE:
                try:
                    self.restore_worktree(
                        operation.worktree_id or "",
                        operation_id=operation.operation_id,
                    )
                except Exception:
                    self.logger.exception(
                        "Worktree restore recovery remains required",
                        extra={"operation_id": operation.operation_id},
                    )
            elif operation.scope is WorktreeLifecycleScope.SESSION_DELETE:
                if operation.worktree_id is None or operation.snapshot_id is None:
                    continue
                try:
                    self.delete_snapshots_for_worktree(operation.worktree_id)
                    if not self._session_exists(operation.session_id):
                        self.manager.lifecycle.update_state(
                            operation.scope,
                            operation.operation_id,
                            WorktreeLifecycleState.COMPLETED,
                        )
                except Exception as error:
                    self._mark_cleanup_required(operation, error)

    def reconcile_storage(self) -> None:
        all_snapshots = self.snapshots.list_all()
        rows = {
            snapshot.id: snapshot
            for snapshot in all_snapshots
            if snapshot.state.value == "ready"
        }
        for snapshot in rows.values():
            try:
                self._verify_snapshot(snapshot)
                if self._read_snapshot_anchor(snapshot) != snapshot.head:
                    raise ValueError("snapshot anchor is missing or changed")
            except (OSError, ValueError, WorktreeError):
                self.snapshots.mark_invalid(snapshot.id)
        known_snapshot_ids = {snapshot.id for snapshot in all_snapshots}
        known_snapshot_ids.update(
            operation.snapshot_id
            for operation in self.manager.lifecycle.list_unfinished()
            if operation.snapshot_id is not None
        )
        for project in self.manager.repository.list_projects():
            if project.git_repository_root is None:
                continue
            try:
                anchors = self.manager.git.list_snapshot_anchors(
                    Path(project.workspace_root)
                )
            except Exception:
                self.logger.warning(
                    "snapshot anchor reconciliation skipped for project",
                    extra={"project_id": project.id},
                )
                continue
            for snapshot_id, head in anchors:
                if snapshot_id not in known_snapshot_ids:
                    self.logger.warning(
                        "orphan snapshot anchor candidate retained",
                        extra={
                            "project_id": project.id,
                            "snapshot_id": snapshot_id,
                        },
                    )
        known_paths = {
            Path(snapshot.artifact_path).resolve()
            for snapshot in all_snapshots
        }
        for snapshot in all_snapshots:
            if snapshot.state.value != "restored":
                continue
            try:
                self._delete_anchor_if_expected(snapshot)
            except (OSError, ValueError, WorktreeError):
                self.logger.warning(
                    "restored Worktree snapshot anchor cleanup deferred",
                    extra={"snapshot_id": snapshot.id},
                )
                continue
            try:
                self.artifacts.delete(snapshot.artifact_path)
            except (OSError, ValueError):
                self.logger.warning(
                    "restored Worktree snapshot artifact cleanup deferred",
                    extra={"snapshot_id": snapshot.id},
                )
        for path in self.artifacts.list_directories():
            if path.resolve() in known_paths:
                continue
            self.logger.warning(
                "orphan snapshot artifact candidate retained",
                extra={"artifact_path": str(path)},
            )

    def _find_or_prepare_cleanup(
        self, worktree: Worktree
    ) -> WorktreeLifecycleOperation:
        for operation in self.manager.lifecycle.list_unfinished():
            if (
                operation.scope is WorktreeLifecycleScope.RETENTION_CLEANUP
                and operation.worktree_id == worktree.id
            ):
                return operation
        now = _now()
        snapshot_id = self._id_factory()
        return self.manager.lifecycle.prepare(
            WorktreeLifecycleOperation(
                scope=WorktreeLifecycleScope.RETENTION_CLEANUP,
                operation_id=f"retention-{worktree.id}-{snapshot_id}",
                state=WorktreeLifecycleState.PREPARED,
                project_id=worktree.project_id,
                repository_root=self.manager.project(worktree.project_id).workspace_root,
                worktree_id=worktree.id,
                worktree_root=worktree.worktree_root,
                base_ref=worktree.base_ref,
                branch=worktree.branch,
                base_commit=worktree.base_commit,
                session_id=self._session_for_worktree(worktree.id),
                snapshot_id=snapshot_id,
                created_at=now,
                updated_at=now,
            )
        )

    def _resume_cleanup(self, operation: WorktreeLifecycleOperation) -> Worktree:
        if operation.worktree_id is None or operation.snapshot_id is None:
            raise WorktreeError("worktree_lifecycle_invalid")
        worktree = self.manager.read_worktree(operation.worktree_id)
        snapshot = self.snapshots.read(operation.snapshot_id)
        if self._has_active_run(worktree.id):
            raise WorktreeError("active_run")
        if self._has_unfinished_handoff(worktree.id):
            raise WorktreeError("unfinished_handoff")
        if snapshot is None:
            if worktree.state is not WorktreeState.ACTIVE:
                raise WorktreeError("worktree_snapshot_required")
            snapshot = self._save_snapshot(worktree, operation.snapshot_id)
        elif snapshot.state.value != "ready":
            raise WorktreeError("worktree_snapshot_required")
        else:
            self._verify_snapshot(snapshot)
        if (
            operation.snapshot_head != snapshot.head
            or operation.snapshot_fingerprint != snapshot.source_fingerprint
        ):
            operation = self.manager.lifecycle.update_snapshot_facts(
                operation.scope,
                operation.operation_id,
                snapshot_id=snapshot.id,
                snapshot_head=snapshot.head,
                snapshot_fingerprint=snapshot.source_fingerprint,
            )
        if operation.state is WorktreeLifecycleState.PREPARED:
            operation = self.manager.lifecycle.update_state(
                operation.scope,
                operation.operation_id,
                WorktreeLifecycleState.SNAPSHOT_SAVED,
            )
        if operation.state is WorktreeLifecycleState.SNAPSHOT_SAVED:
            if worktree.state is not WorktreeState.DELETED:
                if self._physical_worktree_absent(worktree):
                    worktree = self.manager.repository.update_state(
                        worktree.id, WorktreeState.DELETED
                    )
                else:
                    worktree = self.manager.clean_for_retention(worktree.id)
            operation = self.manager.lifecycle.update_state(
                operation.scope,
                operation.operation_id,
                WorktreeLifecycleState.WORKTREE_DELETED,
            )
        if operation.state is WorktreeLifecycleState.WORKTREE_DELETED:
            operation = self.manager.lifecycle.update_state(
                operation.scope,
                operation.operation_id,
                WorktreeLifecycleState.COMPLETED,
            )
        return worktree

    def _save_snapshot(self, worktree: Worktree, snapshot_id: str) -> WorktreeSnapshot:
        root = Path(worktree.worktree_root)
        source = self.manager.source_snapshot(root, include_local_changes=True)
        source_after = self.manager.source_snapshot(root, include_local_changes=True)
        if not _same_snapshot(source, source_after):
            raise WorktreeError("worktree_source_changed")
        if source.changes is None:
            raise WorktreeError("worktree_snapshot_required")
        artifact = self.artifacts.write(snapshot_id, source.changes)
        project = self.manager.project(worktree.project_id)
        self.manager.git.create_snapshot_anchor(Path(project.workspace_root), snapshot_id, source.head)
        now = _now()
        existing = self.snapshots.list_for_worktree(worktree.id)
        if existing:
            latest_created_at = max(snapshot.created_at for snapshot in existing)
            if now <= latest_created_at:
                now = latest_created_at + timedelta(milliseconds=1)
        snapshot = WorktreeSnapshot(
            id=snapshot_id,
            worktree_id=worktree.id,
            session_id=self._session_for_worktree(worktree.id),
            project_id=worktree.project_id,
            base_ref=worktree.base_ref,
            base_commit=worktree.base_commit,
            head=source.head,
            branch=source.branch,
            checkout_branch=worktree.checkout_branch,
            branch_ownership=worktree.branch_ownership,
            dirty=source.status.dirty,
            staged_paths=source.status.staged_paths,
            unstaged_paths=source.status.unstaged_paths,
            untracked_paths=source.status.untracked_paths,
            conflict_paths=source.status.conflict_paths,
            source_fingerprint=source.fingerprint,
            artifact_path=str(artifact.path),
            artifact_sha256=artifact.artifact_sha256,
            full_patch_sha256=artifact.full_patch_sha256,
            staged_patch_sha256=artifact.staged_patch_sha256,
            format_version=artifact.format_version,
            created_at=now,
            updated_at=now,
        )
        try:
            saved = self.snapshots.insert(snapshot)
        except Exception:
            # The anchor and artifact are deliberately retained. The durable
            # lifecycle row is the recovery proof, not a reason to delete data.
            raise
        for older in self.snapshots.list_for_worktree(worktree.id):
            if older.id == saved.id or older.state.value != "ready":
                continue
            try:
                self._delete_anchor_if_expected(older)
                self.artifacts.delete(older.artifact_path)
                self.snapshots.delete(older.id)
            except Exception:
                self.logger.warning(
                    "older Worktree snapshot cleanup deferred",
                    extra={"snapshot_id": older.id, "worktree_id": worktree.id},
                )
        return saved

    def _verify_snapshot_anchor(self, snapshot: WorktreeSnapshot) -> None:
        actual = self._read_snapshot_anchor(snapshot)
        if actual != snapshot.head:
            raise WorktreeError("worktree_snapshot_anchor_mismatch")
        try:
            self.artifacts.verify(snapshot.artifact_path, snapshot.artifact_sha256)
        except (OSError, ValueError) as error:
            raise WorktreeError("worktree_snapshot_checksum_mismatch") from error

    def _verify_snapshot(self, snapshot: WorktreeSnapshot) -> None:
        self._verify_snapshot_anchor(snapshot)
        self._verify_snapshot_artifact(snapshot)

    def _verify_snapshot_artifact(self, snapshot: WorktreeSnapshot) -> None:
        try:
            changes = self.artifacts.read(snapshot.artifact_path)
        except (OSError, ValueError) as error:
            raise WorktreeError("worktree_snapshot_checksum_mismatch") from error
        if (
            _sha256_text(changes.full_patch) != snapshot.full_patch_sha256
            or _sha256_text(changes.staged_patch) != snapshot.staged_patch_sha256
        ):
            raise WorktreeError("worktree_snapshot_checksum_mismatch")

    def _verify_restored_worktree(
        self,
        worktree: Worktree,
        snapshot: WorktreeSnapshot,
    ) -> None:
        validation = self.manager.validate(worktree.id)
        if not validation.valid or validation.head != snapshot.head:
            raise WorktreeError("worktree_restore_verification_failed")
        current = self.manager.source_snapshot(
            Path(worktree.worktree_root), include_local_changes=True
        )
        if (
            current.head != snapshot.head
            or current.fingerprint != snapshot.source_fingerprint
        ):
            raise WorktreeError("worktree_restore_verification_failed")

    def _delete_anchor_if_expected(self, snapshot: WorktreeSnapshot) -> None:
        project = self.manager.project(snapshot.project_id)
        root = Path(project.workspace_root)
        try:
            actual = self.manager.git.snapshot_anchor(root, snapshot.id)
        except Exception as error:
            raise WorktreeError("worktree_snapshot_anchor_unavailable") from error
        if actual is None:
            return
        if actual != snapshot.head:
            raise WorktreeError("worktree_snapshot_anchor_changed")
        try:
            deleted = self.manager.git.delete_snapshot_anchor_if_equals(
                root, snapshot.id, snapshot.head
            )
        except Exception as error:
            raise WorktreeError("worktree_snapshot_anchor_unavailable") from error
        if not deleted:
            raise WorktreeError("worktree_snapshot_anchor_changed")

    def _read_snapshot_anchor(self, snapshot: WorktreeSnapshot) -> str | None:
        project = self.manager.project(snapshot.project_id)
        try:
            return self.manager.git.snapshot_anchor(
                Path(project.workspace_root), snapshot.id
            )
        except Exception as error:
            raise WorktreeError("worktree_snapshot_anchor_unavailable") from error

    def _protected_reason(self, worktree: Worktree) -> str | None:
        if worktree.state is WorktreeState.INVALID:
            return "worktree_recovery_required"
        if worktree.branch_ownership is BranchOwnership.LEGACY_MANAGED:
            return "legacy_managed_branch"
        if self._exists(
            """
            SELECT 1 FROM runs r JOIN sessions s ON s.id = r.session_id
            WHERE (s.worktree_id = ? OR s.associated_worktree_id = ?)
              AND r.status IN ('queued', 'running', 'waiting_approval', 'finalizing')
            LIMIT 1
            """,
            (worktree.id, worktree.id),
        ):
            return "active_run"
        if self._exists(
            """
            SELECT 1 FROM session_handoff_operations
            WHERE associated_worktree_id = ?
              AND state <> 'completed'
            LIMIT 1
            """,
            (worktree.id,),
        ):
            return "unfinished_handoff"
        if self._exists(
            """
            SELECT 1 FROM worktree_lifecycle_operations
            WHERE worktree_id = ? AND state NOT IN ('completed', 'cleanup_required')
            LIMIT 1
            """,
            (worktree.id,),
        ):
            return "unfinished_lifecycle"
        if self._exists(
            """
            SELECT 1 FROM worktree_lifecycle_operations
            WHERE worktree_id = ? AND state = 'cleanup_required'
            LIMIT 1
            """,
            (worktree.id,),
        ):
            return "cleanup_required"
        try:
            validation = self.manager.validate(worktree.id)
        except WorktreeError as error:
            return error.code or "worktree_recovery_required"
        except Exception:
            return "worktree_recovery_required"
        if not validation.valid:
            return validation.code or "worktree_invalid"
        return None

    def _session_for_worktree(self, worktree_id: str) -> str | None:
        with self.database.lock:
            row = self.database.connection().execute(
                "SELECT id FROM sessions WHERE worktree_id = ? "
                "OR associated_worktree_id = ? ORDER BY created_at ASC LIMIT 1",
                (worktree_id, worktree_id),
            ).fetchone()
        return str(row["id"]) if row is not None else None

    def _session_exists(self, session_id: str | None) -> bool:
        if session_id is None:
            return False
        return self._exists("SELECT 1 FROM sessions WHERE id = ?", (session_id,))

    def _has_active_run(self, worktree_id: str) -> bool:
        return self._exists(
            """
            SELECT 1 FROM runs r JOIN sessions s ON s.id = r.session_id
            WHERE (s.worktree_id = ? OR s.associated_worktree_id = ?)
              AND r.status IN ('queued', 'running', 'waiting_approval', 'finalizing')
            LIMIT 1
            """,
            (worktree_id, worktree_id),
        )

    def _has_unfinished_handoff(self, worktree_id: str) -> bool:
        return self._exists(
            """
            SELECT 1 FROM session_handoff_operations
            WHERE associated_worktree_id = ? AND state <> 'completed'
            LIMIT 1
            """,
            (worktree_id,),
        )

    def _mark_cleanup_required(
        self, operation: WorktreeLifecycleOperation, error: Exception
    ) -> None:
        try:
            self.manager.lifecycle.update_state(
                operation.scope,
                operation.operation_id,
                WorktreeLifecycleState.CLEANUP_REQUIRED,
                error_code=(
                    error.code
                    if isinstance(error, WorktreeError)
                    else "worktree_cleanup_required"
                ),
            )
        except Exception:
            self.logger.exception(
                "Worktree cleanup lifecycle state could not be persisted",
                extra={"operation_id": operation.operation_id},
            )

    def _physical_worktree_absent(self, worktree: Worktree) -> bool:
        root = Path(worktree.worktree_root)
        if root.exists() or root.is_symlink():
            return False
        project = self.manager.project(worktree.project_id)
        try:
            return not any(
                entry.worktree_root == str(root.resolve(strict=False))
                for entry in self.manager.git.worktree_list(Path(project.workspace_root))
            )
        except Exception as error:
            raise WorktreeError("worktree_recovery_required") from error

    def _exists(self, sql: str, parameters: tuple[object, ...]) -> bool:
        with self.database.lock:
            return self.database.connection().execute(sql, parameters).fetchone() is not None


def _now() -> datetime:
    current = datetime.now(UTC)
    return current.replace(microsecond=(current.microsecond // 1000) * 1000)


def _same_snapshot(first: GitSourceSnapshot, second: GitSourceSnapshot) -> bool:
    return (
        first.head == second.head
        and first.branch == second.branch
        and first.status == second.status
        and first.fingerprint == second.fingerprint
        and first.changes == second.changes
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = ["RetentionReport", "RetentionSkipped", "WorktreeRetentionService"]
