from __future__ import annotations

from datetime import UTC, datetime
import logging
from pathlib import Path

from eidos_runtime.domain.worktree import (
    Worktree,
    WorktreeLifecycleOperation,
    WorktreeLifecycleScope,
    WorktreeLifecycleState,
    WorktreeState,
)
from eidos_runtime.domain.worktree_snapshot import WorktreeSnapshot
from eidos_runtime.git.errors import WorktreeError
from eidos_runtime.git.manager import WorktreeManager

from eidos_runtime.application.worktree_snapshots import WorktreeSnapshotService


class WorktreeRestoreService:
    """Own same-identity physical restore and durable recovery transitions."""

    def __init__(
        self,
        manager: WorktreeManager,
        snapshots: WorktreeSnapshotService,
        *,
        logger: logging.Logger,
    ) -> None:
        self.manager = manager
        self.snapshots = snapshots
        self.logger = logger

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
            restored_snapshot = self.snapshots.snapshots.read(operation.snapshot_id)
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
                    worktree_root=current_worktree.worktree_root,
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
            in {WorktreeLifecycleState.WORKTREE_REBOUND, WorktreeLifecycleState.COMPLETED}
        ):
            if snapshot.state.value == "ready":
                self.snapshots.verify_artifact(snapshot)
            self._verify_restored_worktree(current_worktree, snapshot)
            self._complete_restored_operation(
                operation, snapshot, allow_missing_anchor=True
            )
            if operation.state is WorktreeLifecycleState.WORKTREE_REBOUND:
                self.manager.lifecycle.update_state(
                    operation.scope,
                    operation.operation_id,
                    WorktreeLifecycleState.COMPLETED,
                )
            return current_worktree
        if operation.state is WorktreeLifecycleState.COMPLETED:
            self._complete_restored_operation(operation, snapshot)
            return self.manager.read_worktree(worktree_id)
        if operation.state is WorktreeLifecycleState.CLEANUP_REQUIRED:
            raise WorktreeError("worktree_restore_required")
        try:
            self.snapshots.verify(snapshot)
            changes = self.snapshots.read_changes(snapshot)
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
                self.manager.lifecycle.update_state(
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
                        error.code
                        if isinstance(error, WorktreeError)
                        else "worktree_restore_failed"
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
            self.snapshots.verify_artifact(snapshot)
            if self.snapshots.read_anchor(snapshot) is None and not allow_missing_anchor:
                raise WorktreeError("worktree_snapshot_anchor_mismatch")
        if snapshot.state.value != "ready" or self.snapshots.read_anchor(snapshot) is not None:
            self.snapshots.delete_anchor_if_expected(snapshot)
        if snapshot.state.value == "ready":
            self.snapshots.snapshots.mark_restored(snapshot.id)
        try:
            self.snapshots.artifacts.delete(snapshot.artifact_path)
        except OSError:
            self.logger.warning(
                "restored Worktree snapshot artifact cleanup deferred",
                extra={"snapshot_id": snapshot.id},
            )

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


def _now() -> datetime:
    current = datetime.now(UTC)
    return current.replace(microsecond=(current.microsecond // 1000) * 1000)


__all__ = ["WorktreeRestoreService"]
