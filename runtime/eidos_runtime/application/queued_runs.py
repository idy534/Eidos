from __future__ import annotations

from collections.abc import Callable

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.db.errors import (
    InvalidRunStateError, OperationConflictError, OperationInProgressError, ResourceNotFoundError,
)
from eidos_runtime.persistence.queued_runs import QueuedRunInputConflict, QueuedRunRepository, QueuedSteerUnsupported, SteerTargetUnavailable
from eidos_runtime.protocol.queued_runs import (
    QueuedRunChangeResponse, QueuedRunDeleteRequest, QueuedRunUpdateRequest,
    QueuedRunListRequest, QueuedRunListResponse,
    QueuedRunSteerRequest,
)
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.sandbox.sensitive import SensitiveContentDenied, SensitiveScanError


class QueuedRunApplication:
    def __init__(
        self, repository: QueuedRunRepository, events: RuntimeEvents,
        scan_text: Callable[[str], str], discard_model_config: Callable[[str], None],
    ) -> None:
        self._repository = repository
        self._events = events
        self._scan_text = scan_text
        self._discard_model_config = discard_model_config

    def list(self, request: QueuedRunListRequest) -> QueuedRunListResponse:
        try:
            page = self._repository.list(request.session_id, request.after_sequence)
        except ResourceNotFoundError as error:
            raise ApplicationError("RESOURCE_NOT_FOUND", str(error)) from error
        return QueuedRunListResponse(items=list(page.items), next_sequence=page.next_sequence)

    def update(self, request: QueuedRunUpdateRequest) -> QueuedRunChangeResponse:
        try:
            text = self._scan_text(request.user_input)
        except SensitiveContentDenied as error:
            raise ApplicationError("SENSITIVE_CONTENT_REJECTED", str(error)) from error
        except SensitiveScanError as error:
            raise ApplicationError("SENSITIVE_SCAN_FAILED", str(error)) from error
        return self._change(request, text)

    def delete(self, request: QueuedRunDeleteRequest) -> QueuedRunChangeResponse:
        result = self._change(request, None)
        self._discard_model_config(request.run_id)
        return result

    def steer(self, request: QueuedRunSteerRequest) -> QueuedRunChangeResponse:
        result = self._change(request, None, target_run_id=request.target_run_id)
        self._discard_model_config(request.run_id)
        return result

    def _change(self, request: QueuedRunDeleteRequest, text: str | None, *, target_run_id: str | None = None) -> QueuedRunChangeResponse:
        try:
            mutation = self._repository.change(
                request.session_id, request.run_id, request.expected_user_input, text,
                operation_id=request.operation_id,
                target_run_id=target_run_id,
            )
        except ResourceNotFoundError as error:
            raise ApplicationError("RESOURCE_NOT_FOUND", str(error)) from error
        except InvalidRunStateError as error:
            raise ApplicationError("QUEUED_RUN_ALREADY_STARTED", str(error)) from error
        except QueuedRunInputConflict as error:
            raise ApplicationError("QUEUED_RUN_INPUT_CONFLICT", str(error)) from error
        except SteerTargetUnavailable as error:
            raise ApplicationError("STEER_TARGET_UNAVAILABLE", str(error)) from error
        except QueuedSteerUnsupported as error:
            raise ApplicationError("QUEUED_RUN_STEER_UNSUPPORTED", str(error)) from error
        except OperationConflictError as error:
            raise ApplicationError("OPERATION_ID_REUSED", str(error)) from error
        except OperationInProgressError as error:
            raise ApplicationError("OPERATION_IN_PROGRESS", str(error)) from error
        except ValueError as error:
            raise ApplicationError("INVALID_PARAMS", str(error)) from error
        self._events.publish(mutation)
        return QueuedRunChangeResponse(change=mutation.value)
