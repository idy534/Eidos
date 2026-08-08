from __future__ import annotations

import logging
from typing import TypeVar

from pydantic import ValidationError

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.runs import RunApplication, RunStartOutcome
from eidos_runtime.db.errors import InvalidRunStateError, ResourceNotFoundError
from eidos_runtime.persistence.response_actions import ResponseActionRepository
from eidos_runtime.protocol.methods import MethodResultDto, RunStartRequestDto
from eidos_runtime.protocol.response_actions import (
    ItemSetFeedbackRequestDto,
    ItemSetFeedbackResponseDto,
    ResponseActionStateRequestDto,
    ResponseActionStateResponseDto,
    RunReviseRequestDto,
    RunReviseResponseDto,
)


logger = logging.getLogger("eidos.runtime.response_actions")
ResultT = TypeVar("ResultT", bound=MethodResultDto)


class ResponseActionApplication:
    """Owns persisted feedback and canonical revision semantics."""

    def __init__(
        self,
        repository: ResponseActionRepository,
        runs: RunApplication,
    ) -> None:
        self._repository = repository
        self._runs = runs

    def state(
        self, request: ResponseActionStateRequestDto
    ) -> ResponseActionStateResponseDto:
        try:
            state = self._repository.state_for_session(request.session_id)
        except ResourceNotFoundError as error:
            raise ApplicationError("RESOURCE_NOT_FOUND", str(error)) from error
        return _result(ResponseActionStateResponseDto, state)

    def set_feedback(
        self, request: ItemSetFeedbackRequestDto
    ) -> ItemSetFeedbackResponseDto:
        try:
            result = self._repository.set_feedback(request.item_id, request.feedback)
        except ResourceNotFoundError as error:
            raise ApplicationError("RESOURCE_NOT_FOUND", str(error)) from error
        except InvalidRunStateError as error:
            raise ApplicationError("INVALID_STATE", str(error)) from error
        logger.info(
            "Assistant response feedback updated",
            extra={"item_id": request.item_id, "feedback": request.feedback},
        )
        return _result(ItemSetFeedbackResponseDto, result)

    def revise(self, request: RunReviseRequestDto) -> RunStartOutcome:
        try:
            source = self._repository.validate_revision_source(request.source_run_id)
        except ResourceNotFoundError as error:
            raise ApplicationError("RESOURCE_NOT_FOUND", str(error)) from error
        except InvalidRunStateError as error:
            raise ApplicationError("INVALID_STATE", str(error)) from error

        revision_kind = "edit" if request.user_input is not None else "regenerate"
        user_input = (
            request.user_input
            if request.user_input is not None
            else str(source["userInput"])
        )
        start_request = RunStartRequestDto.model_validate({
            "sessionId": source["sessionId"],
            "userInput": user_input,
            "modelId": source["modelId"],
            "operationId": request.operation_id,
        })
        outcome = self._runs.start(start_request)
        run_id = str(outcome.response.root["id"])
        try:
            self._repository.record_revision(
                run_id=run_id,
                source_run_id=request.source_run_id,
                revision_kind=revision_kind,
            )
        except (ResourceNotFoundError, InvalidRunStateError) as error:
            # The worker gate is still closed here. Abort the newly-created run
            # so a revision can never execute without its durable lineage fact.
            outcome.mark_response_failed()
            code = (
                "RESOURCE_NOT_FOUND"
                if isinstance(error, ResourceNotFoundError)
                else "INVALID_STATE"
            )
            raise ApplicationError(code, str(error)) from error

        outcome.response = _result(
            RunReviseResponseDto,
            {
                "run": outcome.response.to_json_value(),
                "sourceRunId": request.source_run_id,
                "kind": revision_kind,
            },
        )
        logger.info(
            "Run revision created",
            extra={
                "run_id": run_id,
                "source_run_id": request.source_run_id,
                "revision_kind": revision_kind,
            },
        )
        return outcome


def _result(result_type: type[ResultT], value: object) -> ResultT:
    try:
        return result_type.model_validate(value)
    except ValidationError as error:
        raise ApplicationError(
            "INTERNAL_ERROR", "response action result violates its protocol contract"
        ) from error


__all__ = ["ResponseActionApplication"]
