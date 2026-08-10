from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import ValidationError

from eidos_runtime.application.errors import (
    ApplicationError,
    ApplicationInvalidParamsError,
)
from eidos_runtime.db.errors import (
    OperationConflictError,
    OperationInProgressError,
    ResourceNotFoundError,
)
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.long_task import SafePoint
from eidos_runtime.domain.project import Project
from eidos_runtime.domain.worktree import Worktree
from eidos_runtime.git.errors import WorktreeError
from eidos_runtime.git.status import GitStatusSnapshot
from eidos_runtime.persistence.checkpoints import CheckpointRepository
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

    def project(self, project_id: str) -> Project: ...

    def status(self, worktree_id: str) -> GitStatusSnapshot: ...

    def delete(self, worktree_id: str) -> Worktree: ...


class CheckpointApplication:
    def __init__(
        self,
        store: SessionStore,
        repository: CheckpointRepository,
        *,
        worktree_manager: CheckpointWorktreePort | None = None,
    ) -> None:
        self._store = store
        self._repository = repository
        self._sessions = store.typed_runtime_repository()
        self._worktree_manager = worktree_manager
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
            if request.workspace_root is None:
                raise ApplicationInvalidParamsError(
                    "LEGACY_CHECKPOINT_FORK_WORKSPACE_REQUIRED",
                    "legacy checkpoint fork requires workspaceRoot",
                )
            operation_request["workspaceRoot"] = request.workspace_root
        elif request.workspace_root is not None:
            raise ApplicationInvalidParamsError(
                "MANAGED_CHECKPOINT_FORK_PATH_FORBIDDEN",
                "managed checkpoint fork resolves its Project from the parent Session",
            )

        replay = self._fork_replay(request.operation_id, operation_request)
        if replay is not None:
            return replay

        fork_manager: CheckpointWorktreePort | None = None
        fork_worktree: Worktree | None = None
        if parent_projection.worktree is None:
            assert request.workspace_root is not None
            session = self._store.create_session(request.workspace_root)
            session_id = str(session["id"])
        else:
            if checkpoint.git_head is None:
                raise ApplicationError(
                    "CHECKPOINT_GIT_STATE_UNAVAILABLE",
                    "managed checkpoint has no Git HEAD",
                )
            fork_manager = self._manager_or_error()
            try:
                parent_project = fork_manager.project(
                    parent_projection.worktree.project_id
                )
                fork_worktree = fork_manager.create(
                    parent_project.repository_root,
                    base_ref=checkpoint.git_head,
                )
                session_mutation = self._sessions.create_session(
                    parent_project.repository_root,
                    worktree_id=fork_worktree.id,
                )
            except WorktreeError as error:
                raise ApplicationError(
                    "CHECKPOINT_FORK_WORKTREE_FAILED", str(error)
                ) from error
            except Exception:
                if fork_worktree is not None:
                    self._discard_unbound_worktree(fork_manager, fork_worktree)
                raise
            session_id = session_mutation.value.id

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
            manager.delete(worktree.id)
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
        self._discard_unbound_worktree(manager, worktree)
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


def _validate(model_type: type[ResultT], value: object) -> ResultT:
    try:
        return model_type.model_validate(value)
    except ValidationError as error:
        raise ApplicationError("INTERNAL_ERROR", "invalid checkpoint result") from error


__all__ = ["CheckpointApplication"]
