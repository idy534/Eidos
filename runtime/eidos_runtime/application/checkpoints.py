from __future__ import annotations

from typing import TypeVar

from pydantic import ValidationError

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.long_task import SafePoint
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


class CheckpointApplication:
    def __init__(self, store: SessionStore, repository: CheckpointRepository) -> None:
        self._store = store
        self._repository = repository

    def create(
        self, request: CheckpointCreateRequestDto
    ) -> CheckpointCreateResponseDto:
        try:
            checkpoint = self._repository.create(request.run_id)
        except KeyError as error:
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
        session = self._store.create_session(request.workspace_root)
        run, _item = self._store.enqueue_run(
            str(session["id"]), str(parent["userInput"]), model_id=str(parent["modelId"])
        )
        self._repository.record_action(
            checkpoint_id=checkpoint.id, action="fork", target_run_id=str(run["id"])
        )
        return _validate(CheckpointForkResponseDto, {
            "checkpoint": checkpoint.model_dump(by_alias=True),
            "parentRunId": checkpoint.run_id,
            "run": run,
        })


def _validate(model_type: type[ResultT], value: object) -> ResultT:
    try:
        return model_type.model_validate(value)
    except ValidationError as error:
        raise ApplicationError("INTERNAL_ERROR", "invalid checkpoint result") from error


__all__ = ["CheckpointApplication"]
