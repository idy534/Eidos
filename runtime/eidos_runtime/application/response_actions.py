from __future__ import annotations

import logging
from typing import TypeVar

from pydantic import ValidationError

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.runs import RunApplication, RunStartOutcome
from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.db.errors import InvalidRunStateError, ResourceNotFoundError
from eidos_runtime.persistence.response_actions import ResponseActionRepository
from eidos_runtime.protocol.methods import (
    ItemSetFeedbackRequestDto,
    ItemSetFeedbackResponseDto,
    MethodResultDto,
    RunReviseRequestDto,
    RunReviseResponseDto,
    RunStartRequestDto,
    SessionReadRequestDto,
    SessionReadResponseDto,
)


logger = logging.getLogger("eidos.runtime.response_actions")
ResultT = TypeVar("ResultT", bound=MethodResultDto)


class ResponseActionApplication:
    """Owns feedback and canonical revision semantics for visible responses."""

    def __init__(
        self,
        repository: ResponseActionRepository,
        sessions: SessionApplication,
        runs: RunApplication,
    ) -> None:
        self._repository = repository
        self._sessions = sessions
        self._runs = runs

    def read_snapshot(
        self, request: SessionReadRequestDto
    ) -> SessionReadResponseDto:
        snapshot = self._sessions.read_snapshot(request)
        payload = snapshot.to_json_value()
        runs = payload.get("runs")
        items = payload.get("items")
        if not isinstance(runs, list) or not isinstance(items, list):
            raise ApplicationError("INTERNAL_ERROR", "invalid session snapshot")

        run_ids = [str(run["id"]) for run in runs if isinstance(run, dict) and "id" in run]
        revisions = self._repository.revisions_for_runs(run_ids)
        for run in runs:
            if not isinstance(run, dict):
                continue
            revision = revisions.get(str(run.get("id", "")))
            if revision is None:
                continue
            source_run_id, revision_kind = revision
            run["supersedesRunId"] = source_run_id
            run["revisionKind"] = revision_kind

        item_ids = [
            str(item["id"])
            for item in items
            if isinstance(item, dict) and "id" in item
        ]
        feedback = self._repository.feedback_for_items(item_ids)
        for item in items:
            if not isinstance(item, dict):
                continue
            value = feedback.get(str(item.get("id", "")))
            if value is not None:
                item["feedback"] = value
        return _result(SessionReadResponseDto, payload)

    def set_feedback(
        self, request: ItemSetFeedbackRequestDto
    ) -> ItemSetFeedbackResponseDto:
        try:
            result = self._repository.set_feedback(request.item_id, request.feedback)
        except ResourceNotFoundError as error:
            raise ApplicationError("RESOURCE_NOT_FOUND", str(error)) from error
        except InvalidRunStateError as error:
            raise ApplicationError("INVALID_STATE", str(error)) from error
        logger.info("Assistant response feedback updated", extra={"item_id": request.item_id})
        return _result(ItemSetFeedbackResponseDto, result)

    def revise(self, request: RunReviseRequestDto) -> RunStartOutcome:
        try:
            source = self._repository.validate_revision_source(request.source_run_id)
        except ResourceNotFoundError as error:
            raise ApplicationError("RESOURCE_NOT_FOUND", str(error)) from error
        except InvalidRunStateError as error:
            raise ApplicationError("INVALID_STATE", str(error)) from error

        revision_kind = "edit" if request.user_input is not None else "regenerate"
        user_input = request.user_input if request.user_input is not None else str(source["userInput"])
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
            # The worker gate has not been released yet. Abort the newly-created
            # run so a failed revision never executes without its lineage fact.
            outcome.mark_response_failed()
            code = "RESOURCE_NOT_FOUND" if isinstance(error, ResourceNotFoundError) else "INVALID_STATE"
            raise ApplicationError(code, str(error)) from error

        response_payload = outcome.response.to_json_value()
        response_payload["supersedesRunId"] = request.source_run_id
        response_payload["revisionKind"] = revision_kind
        outcome.response = _result(RunReviseResponseDto, response_payload)
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
