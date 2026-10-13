from __future__ import annotations

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.db.errors import (
    OperationConflictError,
    OperationInProgressError,
    ResourceNotFoundError,
    StorageError,
)
from eidos_runtime.domain.sidebar import SidebarPinsSnapshot
from eidos_runtime.persistence.sidebar import SidebarPinLimitReachedError, SidebarRepository
from eidos_runtime.protocol.sidebar import (
    SidebarListPinsRequest,
    SidebarPinsResponse,
    SidebarSetPinnedRequest,
)
from eidos_runtime.runtime.events import RuntimeEvents


def _response(snapshot: SidebarPinsSnapshot) -> SidebarPinsResponse:
    return SidebarPinsResponse(
        items=list(snapshot.items), through_event_id=snapshot.through_event_id
    )


class SidebarApplication:
    def __init__(self, repository: SidebarRepository, events: RuntimeEvents) -> None:
        self._repository = repository
        self._events = events

    def list_pins(self, _request: SidebarListPinsRequest) -> SidebarPinsResponse:
        return _response(self._repository.list_pins())

    def set_pinned(self, request: SidebarSetPinnedRequest) -> SidebarPinsResponse:
        try:
            mutation = self._repository.set_pinned(
                request.target, request.pinned, operation_id=request.operation_id
            )
        except ResourceNotFoundError as error:
            raise ApplicationError("RESOURCE_NOT_FOUND", str(error)) from error
        except SidebarPinLimitReachedError as error:
            raise ApplicationError("SIDEBAR_PIN_LIMIT_REACHED", str(error)) from error
        except OperationConflictError as error:
            raise ApplicationError("OPERATION_ID_REUSED", str(error)) from error
        except OperationInProgressError as error:
            raise ApplicationError("OPERATION_IN_PROGRESS", str(error)) from error
        except StorageError as error:
            raise ApplicationError("SIDEBAR_PERSISTENCE_FAILED", str(error)) from error
        self._events.publish(mutation)
        return _response(mutation.value)
