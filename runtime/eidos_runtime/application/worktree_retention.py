from __future__ import annotations

from datetime import UTC, datetime
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
from eidos_runtime.git.snapshot_artifacts import SnapshotArtifactStore
from eidos_runtime.persistence.worktree_snapshots import WorktreeSnapshotRepository
from eidos_runtime.persistence.worktree_settings import WorktreeSettingsRepository
from eidos_runtime.models import EidosFrozenStrictModel
from eidos_runtime.persistence.repositories import TypedRuntimeRepository
from eidos_runtime.application.worktree_retention_policy import (
    RetentionCandidate,
    RetentionSkipped,
    WorktreeRetentionPolicy,
)
from eidos_runtime.application.worktree_restore import WorktreeRestoreService
from eidos_runtime.application.worktree_snapshots import WorktreeSnapshotService


class RetentionReport(EidosFrozenStrictModel):
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
        self.persistence = TypedRuntimeRepository(database)
        self.policy = WorktreeRetentionPolicy()
        self.snapshots = snapshot_repository or WorktreeSnapshotRepository(database)
        self.settings = settings_repository or WorktreeSettingsRepository(database)
        data_directory = database.data_directory
        if data_directory is None:
            raise WorktreeError("storage_not_ready")
        self.artifacts = artifact_store or SnapshotArtifactStore(data_directory)
        self._id_factory = id_factory or (lambda: f"ws_{uuid.uuid4().hex}")
        self.logger = logger or logging.getLogger(__name__)
        self.snapshot_service = WorktreeSnapshotService(
            manager,
            self.snapshots,
            self.artifacts,
            session_for_worktree=self._session_for_worktree,
            logger=self.logger,
        )
        self.restore_service = WorktreeRestoreService(
            manager,
            self.snapshot_service,
            logger=self.logger,
        )

    def reconcile(self) -> RetentionReport:
        """Resume 3D operations, then perform one bounded cleanup pass."""

        cleaned: list[str] = []
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
        by_id = {worktree.id: worktree for worktree in worktrees}
        candidates = tuple(
            RetentionCandidate(
                worktree_id=worktree.id,
                managed=worktree.ownership is WorktreeOwnership.MANAGED,
                active=(reason == "active_run"),
                safe=reason is None,
                idle=True,
                last_used_at=int(worktree.last_used_at.timestamp() * 1000),
                created_at=int(worktree.created_at.timestamp() * 1000),
                protection_reason=reason,
            )
            for worktree in worktrees
            for reason in (self._protected_reason(worktree),)
        )
        decision = self.policy.select(
            candidates,
            limit=settings.managed_worktree_limit,
        )
        skipped: list[RetentionSkipped] = list(decision.skipped)
        for worktree_id in decision.cleanup:
            worktree = by_id[worktree_id]
            try:
                self.cleanup_worktree(worktree.id, reason="retention")
            except (WorktreeError, StorageError, OSError, ValueError) as error:
                skipped.append(
                    RetentionSkipped(
                        worktree_id=worktree.id,
                        reason=(
                            error.code
                            if isinstance(error, WorktreeError)
                            else "snapshot_failed"
                        ),
                    )
                )
                continue
            cleaned.append(worktree.id)
        return RetentionReport(
            cleaned_worktree_ids=tuple(cleaned), skipped=tuple(skipped)
        )

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
        return self.restore_service.restore_worktree(
            worktree_id, operation_id=operation_id
        )

    def delete_snapshots_for_worktree(self, worktree_id: str) -> None:
        """Delete snapshot metadata and artifacts for explicit Session delete."""
        self.snapshot_service.delete_for_worktree(worktree_id)

    def has_ready_snapshot(self, worktree_id: str) -> bool:
        return self.snapshot_service.has_ready(worktree_id)

    def latest_ready_snapshot_id(self, worktree_id: str) -> str | None:
        return self.snapshot_service.latest_ready_id(worktree_id)

    def latest_ready_snapshot(self, worktree_id: str) -> WorktreeSnapshot | None:
        return self.snapshot_service.latest_ready(worktree_id)

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
        self.snapshot_service.reconcile_storage()

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
            snapshot = self.snapshot_service.save(worktree, operation.snapshot_id)
        elif snapshot.state.value != "ready":
            raise WorktreeError("worktree_snapshot_required")
        else:
            self.snapshot_service.verify(snapshot)
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
        """Compatibility seam for recovery tests; ownership lives in snapshot service."""

        return self.snapshot_service.save(worktree, snapshot_id)

    def _protected_reason(self, worktree: Worktree) -> str | None:
        if worktree.state is WorktreeState.INVALID:
            return "worktree_recovery_required"
        if worktree.branch_ownership is BranchOwnership.LEGACY_MANAGED:
            return "legacy_managed_branch"
        if self.persistence.has_active_run_for_worktree(worktree.id):
            return "active_run"
        if self.persistence.has_unfinished_handoff_for_worktree(worktree.id):
            return "unfinished_handoff"
        if self.manager.lifecycle.has_unfinished_for_worktree(worktree.id):
            return "unfinished_lifecycle"
        if self.manager.lifecycle.has_cleanup_required(worktree.id):
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
        session = self.persistence.session_for_worktree(worktree_id)
        return session.id if session is not None else None

    def _session_exists(self, session_id: str | None) -> bool:
        if session_id is None:
            return False
        return self.persistence.read_session(session_id) is not None

    def _has_active_run(self, worktree_id: str) -> bool:
        return self.persistence.has_active_run_for_worktree(worktree_id)

    def _has_unfinished_handoff(self, worktree_id: str) -> bool:
        return self.persistence.has_unfinished_handoff_for_worktree(worktree_id)

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

def _now() -> datetime:
    current = datetime.now(UTC)
    return current.replace(microsecond=(current.microsecond // 1000) * 1000)


__all__ = ["RetentionReport", "RetentionSkipped", "WorktreeRetentionService"]
