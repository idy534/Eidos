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
from eidos_runtime.git.errors import WorktreeError
from eidos_runtime.git.status import GitStatusSnapshot
from eidos_runtime.persistence.checkpoints import CheckpointRepository
from eidos_runtime.persistence.worktree_lifecycle import WorktreeLifecycleRepository
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
        try:
            run = self._store.read_run(request.run_id)
            projection = self._sessions.read_session_projection(str(run["sessionId"]))
            if projection is None:
                raise ResourceNotFoundError("session not found")
            git_head: str | None = None
            if projection.worktree is not None:
                manager = self._manager_or_error()
                try:
                    git_head = manager.status(projection.worktree.worktree_id).head
                except WorktreeError as error:
                    raise ApplicationError(
                        "CHECKPOINT_GIT_STATE_UNAVAILABLE", str(error)
                    ) from error
            checkpoint = self._repository.create(
                request.run_id,
                git_head=git_head,
            )
        except (KeyError, ResourceNotFoundError) as error:
            raise ApplicationError("RESOURCE_NOT_FOUND", "run not found") from error
        progress = self._store.long_task_progress(request.run_id)
        if progress is not None:
            self._store.long_task_repository().record_safe_point(
                request.run_id, SafePoint.AFTER_CHECKPOINT
            )
        return _validate(
            CheckpointCreateResponseDto,
            {"checkpoint": checkpoint.model_dump(by_alias=True)},
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
        checkpoint = self._repository.read(request.checkpoint_id)
        if checkpoint is None:
            raise ApplicationError("RESOURCE_NOT_FOUND", "checkpoint not found")
        if not self._repository.workspace_is_compatible(checkpoint):
            raise ApplicationError("INVALID_STATE", "checkpoint workspace changed")
        self._repository.record_action(
            checkpoint_id=checkpoint.id, action="rewind", target_run_id=checkpoint.run_id
        )
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
                        created_at=now,
                        updated_at=now,
                    )
                )
            elif (
                lifecycle_operation.repository_root
                != parent_project.workspace_root
                or lifecycle_operation.checkpoint_id != checkpoint_id
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
    def reconcile(self) -> object: ...


def _validate(model_type: type[ResultT], value: object) -> ResultT:
    try:
        return model_type.model_validate(value)
    except ValidationError as error:
        raise ApplicationError("INTERNAL_ERROR", "invalid checkpoint result") from error


__all__ = ["CheckpointApplication"]
