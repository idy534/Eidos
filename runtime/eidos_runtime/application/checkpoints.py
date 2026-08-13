from __future__ import annotations

import logging
from pathlib import Path
from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Protocol, TypeVar
import uuid

from pydantic import ValidationError

from eidos_runtime.application.errors import (
    ApplicationError,
    ApplicationInvalidParamsError,
)
from eidos_runtime.application.session_lifecycle import SessionLifecycleCoordinator
from eidos_runtime.db.errors import (
    OperationConflictError,
    OperationInProgressError,
    ResourceNotFoundError,
    StorageError,
)
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.long_task import SafePoint
from eidos_runtime.domain.project import Project
from eidos_runtime.domain.session import SessionExecutionMode
from eidos_runtime.domain.checkpoint import Checkpoint
from eidos_runtime.domain.session import SessionProjection
from eidos_runtime.domain.worktree import (
    Worktree,
    WorktreeLifecycleOperation,
    WorktreeLifecycleScope,
    WorktreeLifecycleState,
)
from eidos_runtime.domain.worktree_snapshot import WorktreeSnapshot
from eidos_runtime.git.errors import WorktreeError
from eidos_runtime.git.models import (
    GitOperationState,
    GitSourceSnapshot,
    GitWorkingTreePatch,
)
from eidos_runtime.git.status import GitStatusSnapshot
from eidos_runtime.persistence.checkpoints import CheckpointRepository
from eidos_runtime.persistence.worktree_lifecycle import WorktreeLifecycleRepository
from eidos_runtime.application.worktree_snapshots import WorktreeSnapshotService
from eidos_runtime.protocol.methods import (
    CheckpointCreateRequestDto,
    CheckpointCreateResponseDto,
    CheckpointForkRequestDto,
    CheckpointForkResponseDto,
    CheckpointListRequestDto,
    CheckpointListResponseDto,
    CheckpointRewindRequestDto,
    CheckpointRewindResponseDto,
)
from eidos_runtime.protocol.methods import MethodResultDto


ResultT = TypeVar("ResultT", bound=MethodResultDto)


class CheckpointWorktreePort(Protocol):
    def create(
        self, repository_root: Path | str, base_ref: str | None = None
    ) -> Worktree: ...

    def prepare_create(
        self, repository_root: Path | str, base_ref: str | None = None
    ) -> Worktree: ...

    def create_prepared(
        self,
        plan: Worktree,
        *,
        compensate_on_failure: bool = True,
    ) -> Worktree: ...

    def prepared_from_lifecycle(
        self, operation: WorktreeLifecycleOperation
    ) -> Worktree: ...

    def project(self, project_id: str) -> Project: ...

    def status(self, worktree_id: str) -> GitStatusSnapshot: ...

    def read_worktree(self, worktree_id: str) -> Worktree: ...

    def restore_snapshot_state(
        self,
        worktree_id: str,
        *,
        head: str,
        changes: GitWorkingTreePatch,
        expected_fingerprint: str | None,
    ) -> Worktree: ...

    def source_snapshot(
        self, repository_root: Path, *, include_local_changes: bool
    ) -> GitSourceSnapshot: ...

    def local_operation_state(self, repository_root: Path) -> GitOperationState: ...

    def restore_local_snapshot_state(
        self,
        repository_root: Path,
        *,
        expected_common_dir: Path,
        head: str,
        changes: GitWorkingTreePatch,
        expected_fingerprint: str,
    ) -> None: ...

    def rollback_create(self, worktree_id: str) -> Worktree: ...

    def touch_last_used(self, worktree_id: str) -> Worktree: ...

    @property
    def lifecycle(self) -> WorktreeLifecycleRepository: ...


class CheckpointApplication:
    def __init__(
        self,
        store: SessionStore,
        repository: CheckpointRepository,
        *,
        worktree_manager: CheckpointWorktreePort | None = None,
        lifecycle: SessionLifecycleCoordinator | None = None,
        retention: "CheckpointRetentionPort | None" = None,
    ) -> None:
        self._store = store
        self._repository = repository
        self._sessions = store.typed_runtime_repository()
        self._worktree_manager = worktree_manager
        self._retention = retention
        self._lifecycle = lifecycle or SessionLifecycleCoordinator()
        self._logger = logging.getLogger(__name__)

    def create(
        self, request: CheckpointCreateRequestDto
    ) -> CheckpointCreateResponseDto:
        operation_request: dict[str, object] = {"runId": request.run_id}
        replay = self._create_replay(request.operation_id, operation_request)
        if replay is not None:
            return replay
        try:
            run = self._store.read_run(request.run_id)
            projection = self._sessions.read_session_projection(str(run["sessionId"]))
            if projection is None:
                raise ResourceNotFoundError("session not found")
            git_head: str | None = None
            git_snapshot_id: str | None = None
            checkpoint_id = (
                str(uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"eidos:checkpoint-create:{request.operation_id}",
                ))
                if request.operation_id is not None
                else str(uuid.uuid4())
            )
            existing_checkpoint = self._repository.read(checkpoint_id)
            if existing_checkpoint is not None:
                result = _validate(CheckpointCreateResponseDto, {
                    "checkpoint": existing_checkpoint.model_dump(by_alias=True)
                })
                return self._record_create_operation(
                    request.operation_id, operation_request, result
                )
            if projection.worktree is not None:
                manager = self._manager_or_error()
                try:
                    worktree = manager.read_worktree(projection.worktree.worktree_id)
                    snapshot_id = f"git-{checkpoint_id}"
                    snapshot = self.snapshot_service.snapshots.read(snapshot_id)
                    if snapshot is None:
                        snapshot = self.snapshot_service.save(
                            worktree,
                            snapshot_id,
                            replace_older=False,
                        )
                    self.snapshot_service.verify(snapshot)
                    git_head = snapshot.head
                    git_snapshot_id = snapshot.id
                except (OSError, ValueError, StorageError, WorktreeError) as error:
                    raise ApplicationError(
                        "CHECKPOINT_GIT_STATE_UNAVAILABLE", str(error)
                    ) from error
            elif projection.project.git_available:
                try:
                    manager = self._manager_or_error()
                    project = manager.project(projection.project.id)
                    identity = self._store.workspace_for_run(request.run_id)
                    snapshot_id = f"git-{checkpoint_id}"
                    snapshot = self.snapshot_service.snapshots.read(snapshot_id)
                    if snapshot is None:
                        snapshot = self.snapshot_service.save_local(
                            project,
                            workspace_root=identity.path,
                            session_id=projection.session.id,
                            snapshot_id=snapshot_id,
                        )
                    self.snapshot_service.verify(snapshot)
                    git_head = snapshot.head
                    git_snapshot_id = snapshot.id
                except (OSError, ValueError, StorageError, WorktreeError) as error:
                    raise ApplicationError(
                        "CHECKPOINT_GIT_STATE_UNAVAILABLE", str(error)
                    ) from error
            checkpoint = self._repository.create(
                request.run_id,
                checkpoint_id=checkpoint_id,
                git_head=git_head,
                git_snapshot_id=git_snapshot_id,
            )
        except (KeyError, ResourceNotFoundError) as error:
            raise ApplicationError("RESOURCE_NOT_FOUND", "run not found") from error
        progress = self._store.long_task_progress(request.run_id)
        if progress is not None:
            self._store.long_task_repository().record_safe_point(
                request.run_id, SafePoint.AFTER_CHECKPOINT
            )
        result = _validate(
            CheckpointCreateResponseDto,
            {"checkpoint": checkpoint.model_dump(by_alias=True)},
        )
        return self._record_create_operation(
            request.operation_id, operation_request, result
        )

    def list(self, request: CheckpointListRequestDto) -> CheckpointListResponseDto:
        return _validate(
            CheckpointListResponseDto,
            {"checkpoints": [
                value.model_dump(by_alias=True)
                for value in self._repository.list_for_run(request.run_id)
            ]},
        )

    def rewind(
        self, request: CheckpointRewindRequestDto
    ) -> CheckpointRewindResponseDto:
        operation_request: dict[str, object] = {"checkpointId": request.checkpoint_id}
        replay = self._rewind_replay(request.operation_id, operation_request)
        if replay is not None:
            return replay
        checkpoint = self._repository.read(request.checkpoint_id)
        if checkpoint is None:
            raise ApplicationError("RESOURCE_NOT_FOUND", "checkpoint not found")
        if not self._repository.workspace_is_compatible(checkpoint):
            raise ApplicationError("INVALID_STATE", "checkpoint workspace changed")
        run = self._store.read_run(checkpoint.run_id)
        projection = self._sessions.read_session_projection(str(run["sessionId"]))
        if projection is None:
            raise ApplicationError("RESOURCE_NOT_FOUND", "session not found")
        snapshot = (
            self._ready_checkpoint_snapshot(checkpoint)
            if checkpoint.git_snapshot_id is not None
            else None
        )
        if snapshot is not None and snapshot.worktree_id is None:
            return self._rewind_local(
                request,
                checkpoint=checkpoint,
                projection=projection,
                snapshot=snapshot,
                operation_request=operation_request,
            )
        if projection.worktree is not None:
            return self._rewind_managed(
                request,
                checkpoint=checkpoint,
                projection=projection,
                operation_request=operation_request,
            )
        self._repository.record_action(
            checkpoint_id=checkpoint.id, action="rewind", target_run_id=checkpoint.run_id
        )
        result = self._rewind_result(checkpoint)
        return self._record_rewind_operation(
            request.operation_id, operation_request, result
        )

    def _rewind_local(
        self,
        request: CheckpointRewindRequestDto,
        *,
        checkpoint: Checkpoint,
        projection: SessionProjection,
        snapshot: WorktreeSnapshot,
        operation_request: dict[str, object],
    ) -> CheckpointRewindResponseDto:
        manager = self._manager_or_error()
        project = manager.project(projection.project.id)
        if project.git_common_dir is None or project.git_repository_root is None:
            raise ApplicationError("CHECKPOINT_GIT_STATE_UNAVAILABLE")
        if self._sessions.has_active_run_for_workspace(project.workspace_root):
            raise ApplicationError("CHECKPOINT_WORKFLOW_BUSY")
        workspace_root = Path(snapshot.workspace_root)
        if workspace_root != Path(projection.session.workspace_root):
            raise ApplicationError("CHECKPOINT_GIT_STATE_UNAVAILABLE")
        root = Path(project.git_repository_root)
        try:
            source = manager.source_snapshot(root, include_local_changes=False)
            if source.discovery.git_common_dir != project.git_common_dir:
                raise ApplicationError("CHECKPOINT_GIT_STATE_UNAVAILABLE")
            if manager.local_operation_state(root) is not GitOperationState.NONE:
                raise ApplicationError("GIT_OPERATION_IN_PROGRESS")
        except WorktreeError as error:
            raise ApplicationError(
                "CHECKPOINT_GIT_STATE_UNAVAILABLE", str(error)
            ) from error

        operation_id = request.operation_id or f"checkpoint-rewind-{uuid.uuid4().hex}"
        lifecycle = manager.lifecycle
        operation = lifecycle.read(WorktreeLifecycleScope.CHECKPOINT_REWIND, operation_id)
        if operation is None:
            now = datetime.now(UTC).replace(microsecond=0)
            operation = lifecycle.prepare(WorktreeLifecycleOperation(
                scope=WorktreeLifecycleScope.CHECKPOINT_REWIND,
                operation_id=operation_id,
                state=WorktreeLifecycleState.PREPARED,
                project_id=project.id,
                repository_root=project.git_repository_root,
                worktree_id=None,
                worktree_root=str(workspace_root),
                base_ref=snapshot.base_ref,
                base_commit=snapshot.base_commit,
                branch=snapshot.branch,
                session_id=projection.session.id,
                run_id=checkpoint.run_id,
                checkpoint_id=checkpoint.id,
                snapshot_id=snapshot.id,
                snapshot_head=snapshot.head,
                snapshot_fingerprint=snapshot.source_fingerprint,
                created_at=now,
                updated_at=now,
            ))
        elif (
            operation.checkpoint_id != checkpoint.id
            or operation.snapshot_id != snapshot.id
            or operation.repository_root != str(root)
            or operation.worktree_root != str(workspace_root)
        ):
            raise ApplicationError("OPERATION_ID_REUSED")
        if operation.state is WorktreeLifecycleState.CLEANUP_REQUIRED:
            raise ApplicationError("WORKTREE_RECOVERY_REQUIRED")
        try:
            if operation.state is WorktreeLifecycleState.PREPARED:
                manager.restore_local_snapshot_state(
                    root,
                    expected_common_dir=Path(project.git_common_dir),
                    head=snapshot.head,
                    changes=self.snapshot_service.read_changes(snapshot),
                    expected_fingerprint=snapshot.source_fingerprint,
                )
                operation = lifecycle.update_state(
                    operation.scope,
                    operation.operation_id,
                    WorktreeLifecycleState.STATE_MATERIALIZED,
                )
            action_id = "checkpoint-rewind-action-" + uuid.uuid5(
                uuid.NAMESPACE_URL, f"eidos:checkpoint-rewind:{operation_id}"
            ).hex
            if not self._repository.action_exists(action_id):
                self._repository.record_action(
                    checkpoint_id=checkpoint.id,
                    action="rewind",
                    target_run_id=checkpoint.run_id,
                    action_id=action_id,
                )
            if operation.state is WorktreeLifecycleState.STATE_MATERIALIZED:
                operation = lifecycle.update_state(
                    operation.scope,
                    operation.operation_id,
                    WorktreeLifecycleState.COMPLETED,
                )
        except Exception as error:
            self._repository.mark_reconciliation_required(checkpoint.id)
            try:
                lifecycle.update_state(
                    operation.scope,
                    operation.operation_id,
                    WorktreeLifecycleState.CLEANUP_REQUIRED,
                    error_code=(
                        error.code if isinstance(error, WorktreeError)
                        else "checkpoint_rewind_failed"
                    ),
                )
            except Exception:
                self._logger.exception(
                    "local checkpoint rewind failure state could not be saved"
                )
            if isinstance(error, WorktreeError):
                raise ApplicationError("CHECKPOINT_REWIND_FAILED") from error
            raise
        result = self._rewind_result(checkpoint)
        return self._record_rewind_operation(
            request.operation_id, operation_request, result
        )

    def _rewind_result(self, checkpoint: Checkpoint) -> CheckpointRewindResponseDto:
        run = self._store.read_run(checkpoint.run_id)
        progress = self._store.long_task_progress(checkpoint.run_id)
        return _validate(CheckpointRewindResponseDto, {
            "checkpoint": checkpoint.model_dump(by_alias=True),
            "run": run,
            "task": progress.model_dump(by_alias=True) if progress is not None else None,
            "resumeVerification": (
                progress.last_verification.model_dump(by_alias=True)
                if progress is not None and progress.last_verification is not None
                else None
            ),
        })

    def _rewind_managed(
        self,
        request: CheckpointRewindRequestDto,
        *,
        checkpoint: Checkpoint,
        projection: SessionProjection,
        operation_request: dict[str, object],
    ) -> CheckpointRewindResponseDto:
        manager = self._manager_or_error()
        worktree = projection.worktree
        assert worktree is not None
        if self._sessions.has_active_run_for_worktree(worktree.worktree_id):
            raise ApplicationError("CHECKPOINT_WORKFLOW_BUSY")
        snapshot = self._ready_checkpoint_snapshot(checkpoint)
        operation_id = request.operation_id or f"checkpoint-rewind-{uuid.uuid4().hex}"
        lifecycle = manager.lifecycle
        operation = lifecycle.read(WorktreeLifecycleScope.CHECKPOINT_REWIND, operation_id)
        if operation is None:
            current = manager.read_worktree(worktree.worktree_id)
            operation = lifecycle.prepare(WorktreeLifecycleOperation(
                scope=WorktreeLifecycleScope.CHECKPOINT_REWIND,
                operation_id=operation_id,
                state=WorktreeLifecycleState.PREPARED,
                project_id=current.project_id,
                repository_root=manager.project(current.project_id).workspace_root,
                worktree_id=current.id,
                worktree_root=current.worktree_root,
                base_ref=current.base_ref,
                base_commit=current.base_commit,
                branch=current.branch,
                session_id=projection.session.id,
                run_id=checkpoint.run_id,
                checkpoint_id=checkpoint.id,
                snapshot_id=snapshot.id,
                snapshot_head=snapshot.head,
                snapshot_fingerprint=snapshot.source_fingerprint,
                created_at=datetime.now(UTC).replace(microsecond=0),
                updated_at=datetime.now(UTC).replace(microsecond=0),
            ))
        elif operation.checkpoint_id != checkpoint.id or operation.snapshot_id != snapshot.id:
            raise ApplicationError("OPERATION_ID_REUSED")
        if operation.state is WorktreeLifecycleState.CLEANUP_REQUIRED:
            raise ApplicationError("WORKTREE_RECOVERY_REQUIRED")
        try:
            if operation.state is WorktreeLifecycleState.PREPARED:
                manager.restore_snapshot_state(
                    worktree.worktree_id,
                    head=snapshot.head,
                    changes=self.snapshot_service.read_changes(snapshot),
                    expected_fingerprint=snapshot.source_fingerprint,
                )
                operation = lifecycle.update_state(
                    operation.scope,
                    operation.operation_id,
                    WorktreeLifecycleState.STATE_MATERIALIZED,
                )
            action_id = "checkpoint-rewind-action-" + uuid.uuid5(
                uuid.NAMESPACE_URL, f"eidos:checkpoint-rewind:{operation_id}"
            ).hex
            if not self._repository.action_exists(action_id):
                self._repository.record_action(
                    checkpoint_id=checkpoint.id,
                    action="rewind",
                    target_run_id=checkpoint.run_id,
                    action_id=action_id,
                )
            if operation.state is WorktreeLifecycleState.STATE_MATERIALIZED:
                operation = lifecycle.update_state(
                    operation.scope,
                    operation.operation_id,
                    WorktreeLifecycleState.COMPLETED,
                )
        except Exception as error:
            try:
                lifecycle.update_state(
                    operation.scope,
                    operation.operation_id,
                    WorktreeLifecycleState.CLEANUP_REQUIRED,
                    error_code=(
                        error.code if isinstance(error, WorktreeError)
                        else "checkpoint_rewind_failed"
                    ),
                )
            except Exception:
                self._logger.exception("checkpoint rewind failure state could not be saved")
            if isinstance(error, ApplicationError):
                raise
            if isinstance(error, WorktreeError):
                raise ApplicationError("CHECKPOINT_REWIND_FAILED") from error
            raise
        result = self._rewind_result(checkpoint)
        return self._record_rewind_operation(
            request.operation_id, operation_request, result
        )

    def fork(self, request: CheckpointForkRequestDto) -> CheckpointForkResponseDto:
        operation_guard = (
            self._lifecycle.hold_operation(
                "checkpoint/fork", request.operation_id
            )
            if request.operation_id is not None
            else nullcontext()
        )
        with operation_guard:
            return self._fork(request)

    def _fork(self, request: CheckpointForkRequestDto) -> CheckpointForkResponseDto:
        checkpoint = self._repository.read(request.checkpoint_id)
        if checkpoint is None:
            raise ApplicationError("RESOURCE_NOT_FOUND", "checkpoint not found")
        parent = self._store.read_run(checkpoint.run_id)
        parent_projection = self._sessions.read_session_projection(
            str(parent["sessionId"])
        )
        if parent_projection is None:
            raise ApplicationError("RESOURCE_NOT_FOUND", "parent session not found")
        operation_request: dict[str, object] = {"checkpointId": checkpoint.id}
        if parent_projection.worktree is None:
            if request.workspace_root is not None:
                raise ApplicationInvalidParamsError(
                    "DIRECT_CHECKPOINT_FORK_PATH_FORBIDDEN",
                    "direct checkpoint fork uses the parent Project workspace",
                )
        elif request.workspace_root is not None:
            raise ApplicationInvalidParamsError(
                "MANAGED_CHECKPOINT_FORK_PATH_FORBIDDEN",
                "managed checkpoint fork resolves its Project from the parent Session",
            )

        replay = self._fork_replay(request.operation_id, operation_request)
        if replay is not None:
            return replay

        if parent_projection.worktree is None:
            # A direct fork creates a new runtime/conversation lineage while
            # both Sessions continue to use the same real filesystem root.
            session = self._store.create_session(
                parent_projection.project.workspace_root
            )
            session_id = str(session["id"])
        else:
            return self._fork_managed(
                request,
                checkpoint=checkpoint,
                parent=parent,
                parent_projection=parent_projection,
                operation_request=operation_request,
            )

        fork_manager: CheckpointWorktreePort | None = None
        fork_worktree: Worktree | None = None

        try:
            run, _item = self._store.enqueue_run(
                session_id,
                str(parent["userInput"]),
                model_id=str(parent["modelId"]),
            )
        except Exception:
            if fork_manager is not None and fork_worktree is not None:
                self._discard_fork_session(
                    fork_manager,
                    session_id,
                    fork_worktree,
                )
            raise
        self._repository.record_action(
            checkpoint_id=checkpoint.id, action="fork", target_run_id=str(run["id"])
        )
        result = _validate(CheckpointForkResponseDto, {
            "checkpoint": checkpoint.model_dump(by_alias=True),
            "parentRunId": checkpoint.run_id,
            "run": run,
        })
        return self._record_fork_operation(
            request.operation_id,
            operation_request,
            result,
        )

    def _manager_or_error(self) -> CheckpointWorktreePort:
        if self._worktree_manager is None:
            raise ApplicationError(
                "INTERNAL_ERROR", "managed checkpoint has no Worktree boundary"
            )
        return self._worktree_manager

    @property
    def snapshot_service(self) -> WorktreeSnapshotService:
        if self._retention is None:
            raise ApplicationError(
                "INTERNAL_ERROR", "managed checkpoint has no Snapshot boundary"
            )
        return self._retention.snapshot_service

    def _ready_checkpoint_snapshot(self, checkpoint: Checkpoint) -> WorktreeSnapshot:
        snapshot_id = checkpoint.git_snapshot_id
        if snapshot_id is None:
            raise ApplicationError(
                "CHECKPOINT_GIT_STATE_UNAVAILABLE",
                "managed checkpoint has no Git workspace snapshot",
            )
        snapshot = self.snapshot_service.snapshots.read(snapshot_id)
        if snapshot is None or snapshot.state.value != "ready":
            raise ApplicationError(
                "CHECKPOINT_GIT_STATE_UNAVAILABLE",
                "managed checkpoint Git snapshot is unavailable",
            )
        try:
            self.snapshot_service.verify(snapshot)
        except WorktreeError as error:
            raise ApplicationError(
                "CHECKPOINT_GIT_STATE_UNAVAILABLE", str(error)
            ) from error
        return snapshot

    def _fork_managed(
        self,
        request: CheckpointForkRequestDto,
        *,
        checkpoint: Checkpoint,
        parent: dict[str, object],
        parent_projection: SessionProjection,
        operation_request: dict[str, object],
    ) -> CheckpointForkResponseDto:
        checkpoint_id = str(checkpoint.id)
        git_head = checkpoint.git_head
        if git_head is None:
            raise ApplicationError(
                "CHECKPOINT_GIT_STATE_UNAVAILABLE",
                "managed checkpoint has no Git HEAD",
            )
        manager = self._manager_or_error()
        snapshot = self._ready_checkpoint_snapshot(checkpoint)
        parent_worktree = parent_projection.worktree
        assert parent_worktree is not None
        parent_project = manager.project(parent_worktree.project_id)
        lifecycle = manager.lifecycle
        lifecycle_operation_id = request.operation_id or (
            f"checkpoint-fork-{uuid.uuid4().hex}"
        )
        lifecycle_operation = lifecycle.read(
            WorktreeLifecycleScope.CHECKPOINT_FORK,
            lifecycle_operation_id,
        )
        worktree: Worktree | None = None
        session_id: str | None = None
        try:
            if lifecycle_operation is None:
                plan = manager.prepare_create(
                    parent_project.workspace_root,
                    base_ref=git_head,
                )
                token = uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"eidos:{WorktreeLifecycleScope.CHECKPOINT_FORK.value}:"
                    f"{lifecycle_operation_id}",
                ).hex
                session_id = f"fork-session-{token}"
                run_id = f"fork-run-{token}"
                now = datetime.now(UTC).replace(microsecond=0)
                lifecycle_operation = lifecycle.prepare(
                    WorktreeLifecycleOperation(
                        scope=WorktreeLifecycleScope.CHECKPOINT_FORK,
                        operation_id=lifecycle_operation_id,
                        state=WorktreeLifecycleState.PREPARED,
                        project_id=plan.project_id,
                        repository_root=parent_project.workspace_root,
                        worktree_id=plan.id,
                        worktree_root=plan.worktree_root,
                        base_ref=plan.base_ref,
                        branch=plan.branch,
                        base_commit=plan.base_commit,
                        session_id=session_id,
                        run_id=run_id,
                        checkpoint_id=checkpoint_id,
                        snapshot_id=snapshot.id,
                        snapshot_head=snapshot.head,
                        snapshot_fingerprint=snapshot.source_fingerprint,
                        created_at=now,
                        updated_at=now,
                    )
                )
            elif (
                lifecycle_operation.repository_root
                != parent_project.workspace_root
                or lifecycle_operation.checkpoint_id != checkpoint_id
                or lifecycle_operation.snapshot_id != snapshot.id
                or lifecycle_operation.snapshot_head != snapshot.head
                or lifecycle_operation.snapshot_fingerprint
                != snapshot.source_fingerprint
            ):
                raise ApplicationError(
                    "OPERATION_ID_REUSED",
                    "checkpoint fork operation identity changed",
                )
            if lifecycle_operation.state is WorktreeLifecycleState.CLEANUP_REQUIRED:
                raise ApplicationError(
                    "WORKTREE_RECOVERY_REQUIRED",
                    lifecycle_operation.error_code
                    or "checkpoint fork recovery is required",
                )
            if lifecycle_operation.session_id is None or lifecycle_operation.run_id is None:
                raise ApplicationError(
                    "WORKTREE_RECOVERY_REQUIRED",
                    "checkpoint fork lifecycle ids are incomplete",
                )
            session_id = lifecycle_operation.session_id
            plan = manager.prepared_from_lifecycle(lifecycle_operation)
            worktree = manager.create_prepared(
                plan,
                compensate_on_failure=False,
            )
            if lifecycle_operation.state is WorktreeLifecycleState.PREPARED:
                lifecycle_operation = lifecycle.update_state(
                    WorktreeLifecycleScope.CHECKPOINT_FORK,
                    lifecycle_operation_id,
                    WorktreeLifecycleState.WORKTREE_CREATED,
                )

            changes = self.snapshot_service.read_changes(snapshot)
            manager.restore_snapshot_state(
                worktree.id,
                head=snapshot.head,
                changes=changes,
                expected_fingerprint=None,
            )
            materialized = manager.source_snapshot(
                Path(worktree.worktree_root), include_local_changes=True
            )
            if (
                materialized.head != snapshot.head
                or materialized.changes != changes
                or materialized.status.staged_paths != snapshot.staged_paths
                or materialized.status.unstaged_paths != snapshot.unstaged_paths
                or materialized.status.untracked_paths != snapshot.untracked_paths
                or materialized.status.conflict_paths != snapshot.conflict_paths
            ):
                raise ApplicationError(
                    "CHECKPOINT_FORK_WORKTREE_FAILED",
                    "checkpoint Git state verification failed",
                )

            session = self._sessions.read_session(session_id)
            if session is None:
                session_mutation = self._sessions.create_session(
                    parent_project.workspace_root,
                    worktree_id=worktree.id,
                    execution_mode=SessionExecutionMode.WORKTREE,
                    project_id=parent_project.id,
                    operation_id=None,
                    session_id=session_id,
                )
                session = session_mutation.value
            elif session.worktree_id != worktree.id:
                raise ApplicationError(
                    "WORKTREE_RECOVERY_REQUIRED",
                    "checkpoint fork session binding does not match",
                )
            if lifecycle_operation.state in {
                WorktreeLifecycleState.PREPARED,
                WorktreeLifecycleState.WORKTREE_CREATED,
            }:
                lifecycle_operation = lifecycle.update_state(
                    WorktreeLifecycleScope.CHECKPOINT_FORK,
                    lifecycle_operation_id,
                    WorktreeLifecycleState.SESSION_CREATED,
                )

            try:
                run = self._store.read_run(lifecycle_operation.run_id)
            except ResourceNotFoundError:
                run, _item = self._store.enqueue_run(
                    session_id,
                    str(parent["userInput"]),
                    model_id=str(parent["modelId"]),
                    operation_id=None,
                    run_id=lifecycle_operation.run_id,
                    item_id=f"{lifecycle_operation.run_id}-item",
                )
            if lifecycle_operation.state is WorktreeLifecycleState.SESSION_CREATED:
                lifecycle_operation = lifecycle.update_state(
                    WorktreeLifecycleScope.CHECKPOINT_FORK,
                    lifecycle_operation_id,
                    WorktreeLifecycleState.RUN_CREATED,
                )

            action_id = (
                "checkpoint-fork-action-"
                + uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"eidos:checkpoint-action:{lifecycle_operation_id}",
                ).hex
            )
            if not self._repository.action_exists(action_id):
                self._repository.record_action(
                    checkpoint_id=checkpoint_id,
                    action="fork",
                    target_run_id=lifecycle_operation.run_id,
                    action_id=action_id,
                )
            if lifecycle_operation.state is WorktreeLifecycleState.RUN_CREATED:
                lifecycle_operation = lifecycle.update_state(
                    WorktreeLifecycleScope.CHECKPOINT_FORK,
                    lifecycle_operation_id,
                    WorktreeLifecycleState.CHECKPOINT_ACTION_CREATED,
                )
            lifecycle.update_state(
                WorktreeLifecycleScope.CHECKPOINT_FORK,
                lifecycle_operation_id,
                WorktreeLifecycleState.COMPLETED,
            )
            manager.touch_last_used(worktree.id)
            if self._retention is not None:
                try:
                    self._retention.reconcile()
                except Exception:
                    self._logger.exception(
                        "Worktree retention reconciliation after checkpoint fork failed",
                        extra={"worktree_id": worktree.id},
                    )
            result = _validate(CheckpointForkResponseDto, {
                "checkpoint": checkpoint.model_dump(by_alias=True),
                "parentRunId": checkpoint.run_id,
                "run": run,
            })
            return self._record_fork_operation(
                request.operation_id,
                operation_request,
                result,
            )
        except StorageError:
            if worktree is not None and session_id is not None:
                self._discard_fork_session(manager, session_id, worktree)
                try:
                    lifecycle.update_state(
                        WorktreeLifecycleScope.CHECKPOINT_FORK,
                        lifecycle_operation_id,
                        WorktreeLifecycleState.CLEANUP_REQUIRED,
                        error_code="checkpoint_fork_persistence_failed",
                    )
                except Exception:
                    pass
            raise
        except WorktreeError as error:
            raise ApplicationError(
                "CHECKPOINT_FORK_WORKTREE_FAILED", str(error)
            ) from error
    def _fork_replay(
        self,
        operation_id: str | None,
        operation_request: dict[str, object],
    ) -> CheckpointForkResponseDto | None:
        if operation_id is None:
            return None
        try:
            replay = self._store.operation_result(
                operation_id,
                "checkpoint/fork",
                operation_request,
            )
        except OperationConflictError as error:
            raise ApplicationError("OPERATION_ID_REUSED", str(error)) from error
        except OperationInProgressError as error:
            raise ApplicationError("OPERATION_IN_PROGRESS", str(error)) from error
        return (
            _validate(CheckpointForkResponseDto, replay)
            if replay is not None
            else None
        )

    def _create_replay(
        self,
        operation_id: str | None,
        operation_request: dict[str, object],
    ) -> CheckpointCreateResponseDto | None:
        if operation_id is None:
            return None
        try:
            replay = self._store.operation_result(
                operation_id, "checkpoint/create", operation_request
            )
        except OperationConflictError as error:
            raise ApplicationError("OPERATION_ID_REUSED", str(error)) from error
        except OperationInProgressError as error:
            raise ApplicationError("OPERATION_IN_PROGRESS", str(error)) from error
        return (
            _validate(CheckpointCreateResponseDto, replay)
            if replay is not None else None
        )

    def _record_create_operation(
        self,
        operation_id: str | None,
        operation_request: dict[str, object],
        result: CheckpointCreateResponseDto,
    ) -> CheckpointCreateResponseDto:
        if operation_id is None:
            return result
        try:
            recorded = self._store.record_operation_result(
                operation_id,
                "checkpoint/create",
                operation_request,
                result.to_json_value(),
            )
        except OperationConflictError as error:
            raise ApplicationError("OPERATION_ID_REUSED", str(error)) from error
        except OperationInProgressError as error:
            raise ApplicationError("OPERATION_IN_PROGRESS", str(error)) from error
        return _validate(CheckpointCreateResponseDto, recorded)

    def _rewind_replay(
        self,
        operation_id: str | None,
        operation_request: dict[str, object],
    ) -> CheckpointRewindResponseDto | None:
        if operation_id is None:
            return None
        try:
            replay = self._store.operation_result(
                operation_id, "checkpoint/rewind", operation_request
            )
        except OperationConflictError as error:
            raise ApplicationError("OPERATION_ID_REUSED", str(error)) from error
        except OperationInProgressError:
            # The Worktree lifecycle record is the recovery authority for this
            # external side effect. Continue reconciliation below.
            return None
        return (
            _validate(CheckpointRewindResponseDto, replay)
            if replay is not None else None
        )

    def _record_rewind_operation(
        self,
        operation_id: str | None,
        operation_request: dict[str, object],
        result: CheckpointRewindResponseDto,
    ) -> CheckpointRewindResponseDto:
        if operation_id is None:
            return result
        try:
            recorded = self._store.record_operation_result(
                operation_id,
                "checkpoint/rewind",
                operation_request,
                result.to_json_value(),
            )
        except OperationConflictError as error:
            raise ApplicationError("OPERATION_ID_REUSED", str(error)) from error
        except OperationInProgressError as error:
            raise ApplicationError("OPERATION_IN_PROGRESS", str(error)) from error
        return _validate(CheckpointRewindResponseDto, recorded)

    def _record_fork_operation(
        self,
        operation_id: str | None,
        operation_request: dict[str, object],
        result: CheckpointForkResponseDto,
    ) -> CheckpointForkResponseDto:
        if operation_id is None:
            return result
        try:
            recorded = self._store.record_operation_result(
                operation_id,
                "checkpoint/fork",
                operation_request,
                result.to_json_value(),
            )
        except OperationConflictError as error:
            raise ApplicationError("OPERATION_ID_REUSED", str(error)) from error
        except OperationInProgressError as error:
            raise ApplicationError("OPERATION_IN_PROGRESS", str(error)) from error
        return _validate(CheckpointForkResponseDto, recorded)

    def _discard_unbound_worktree(
        self, manager: CheckpointWorktreePort, worktree: Worktree
    ) -> None:
        try:
            manager.rollback_create(worktree.id)
        except WorktreeError as error:
            self._logger.warning(
                "checkpoint fork Worktree cleanup failed",
                extra={
                    "worktree_id": worktree.id,
                    "operation": "checkpoint_fork_cleanup",
                    "error_code": error.code,
                },
            )

    def _discard_fork_session(
        self,
        manager: CheckpointWorktreePort,
        session_id: str,
        worktree: Worktree,
    ) -> None:
        try:
            self._sessions.delete_session(session_id)
        except Exception as error:
            self._logger.warning(
                "checkpoint fork Session cleanup failed",
                extra={
                    "session_id": session_id,
                    "worktree_id": worktree.id,
                    "operation": "checkpoint_fork_cleanup",
                    "error_type": type(error).__name__,
                },
            )
            return
        self._discard_unbound_worktree(manager, worktree)


class CheckpointRetentionPort(Protocol):
    snapshot_service: WorktreeSnapshotService

    def reconcile(self) -> object: ...


def _validate(model_type: type[ResultT], value: object) -> ResultT:
    try:
        return model_type.model_validate(value)
    except ValidationError as error:
        raise ApplicationError("INTERNAL_ERROR", "invalid checkpoint result") from error


__all__ = ["CheckpointApplication"]
