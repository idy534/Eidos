from __future__ import annotations

from collections.abc import Callable
import unicodedata
from typing import Protocol, TypeVar

from pydantic import ValidationError

from eidos_runtime.application.errors import (
    ApplicationError,
    ApplicationInvalidParamsError,
)
from eidos_runtime.db.database import CommittedMutation
from eidos_runtime.db.errors import (
    InvalidCursorError,
    OperationConflictError,
    OperationInProgressError,
    ResourceNotFoundError,
    SessionActiveError,
    WorkspaceBoundaryError,
)
from eidos_runtime.protocol.methods import (
    EventListRequestDto,
    EventListResponseDto,
    MethodResultDto,
    SessionCreateRequestDto,
    SessionCreateResponseDto,
    SessionDeleteRequestDto,
    SessionDeleteResponseDto,
    SessionListRequestDto,
    SessionListResponseDto,
    SessionReadRequestDto,
    SessionReadResponseDto,
    SessionRenameRequestDto,
    SessionRenameResponseDto,
)
from eidos_runtime.domain.session import DeletedSession, Session, SessionPage
from eidos_runtime.persistence.mappers.session import (
    deleted_session_to_legacy_dict,
    session_page_to_legacy_dict,
    session_to_legacy_dict,
)
from eidos_runtime.sandbox.sensitive import (
    SensitiveContentDenied,
    SensitiveScanError,
)


MAX_SESSION_TITLE_BYTES = 120
ResultT = TypeVar("ResultT", bound=MethodResultDto)


class TypedSessionRepositoryPort(Protocol):
    """Typed Session reads/writes consumed by the application layer."""

    def create_session(
        self, workspace_root: str, *, operation_id: str | None = None
    ) -> CommittedMutation[Session]: ...

    def list_sessions(
        self, *, limit: int, cursor: str | None
    ) -> SessionPage: ...

    def read_session(self, session_id: str) -> Session | None: ...

    def rename_session(
        self,
        session_id: str,
        title: str,
        *,
        operation_id: str | None = None,
    ) -> CommittedMutation[Session]: ...

    def delete_session(
        self, session_id: str, *, operation_id: str | None = None
    ) -> CommittedMutation[DeletedSession]: ...


class SessionStorePort(Protocol):
    """Compatibility store for aggregate reads not yet represented as domains.

    Session create/list/read/rename/delete use ``TypedSessionRepositoryPort``.
    The aggregate snapshot and historical event feeds retain their established
    wire records until those records have their own domain models.
    """

    def typed_runtime_repository(self) -> TypedSessionRepositoryPort: ...

    def read_session_snapshot(
        self,
        session_id: str,
        *,
        item_limit: int,
        before_item_id: str | None,
    ) -> dict[str, object]: ...

    def list_events(
        self, session_id: str, *, after_event_id: int, limit: int
    ) -> dict[str, object]: ...


def clean_session_title(value: str) -> str:
    """Apply the established title canonicalization before durable storage."""

    title = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in value
    )
    title = " ".join(title.split()).strip("\"'`“”‘’ ")[:60]
    return title.encode("utf-8")[:MAX_SESSION_TITLE_BYTES].decode(
        "utf-8", errors="ignore"
    ).strip()


class SessionApplication:
    """Owns top-level Session and Session-event use cases.

    Typed Session writes/reads flow through the public repository seam.  The
    Store remains the single transactional authority; aggregate snapshots and
    historical event feeds keep their established compatibility records until
    they receive dedicated domain models.
    """

    def __init__(
        self,
        store: SessionStorePort,
        *,
        scan_text: Callable[[str], str],
    ) -> None:
        self._store = store
        self._repository: TypedSessionRepositoryPort = (
            store.typed_runtime_repository()
        )
        self._scan_text = scan_text

    def create(self, request: SessionCreateRequestDto) -> SessionCreateResponseDto:
        try:
            mutation = self._repository.create_session(
                request.workspace_root,
                operation_id=request.operation_id,
            )
        except WorkspaceBoundaryError as error:
            raise ApplicationError(
                "WORKSPACE_BOUNDARY_VIOLATION", str(error)
            ) from error
        except OperationConflictError as error:
            raise ApplicationError("OPERATION_ID_REUSED", str(error)) from error
        except OperationInProgressError as error:
            raise ApplicationError("OPERATION_IN_PROGRESS", str(error)) from error
        return _result(SessionCreateResponseDto, session_to_legacy_dict(mutation.value))

    def list(self, request: SessionListRequestDto) -> SessionListResponseDto:
        try:
            page = self._repository.list_sessions(
                limit=request.limit,
                cursor=request.cursor,
            )
        except InvalidCursorError as error:
            raise ApplicationInvalidParamsError("INVALID_CURSOR", str(error)) from error
        return _result(SessionListResponseDto, session_page_to_legacy_dict(page))

    def read_snapshot(
        self, request: SessionReadRequestDto
    ) -> SessionReadResponseDto:
        if self._repository.read_session(request.session_id) is None:
            raise ApplicationError("RESOURCE_NOT_FOUND", "session not found")
        try:
            snapshot = self._store.read_session_snapshot(
                request.session_id,
                item_limit=request.item_limit,
                before_item_id=request.before_item_id,
            )
        except ResourceNotFoundError as error:
            raise ApplicationError("RESOURCE_NOT_FOUND", str(error)) from error
        return _result(SessionReadResponseDto, snapshot)

    def rename(self, request: SessionRenameRequestDto) -> SessionRenameResponseDto:
        title = clean_session_title(request.title)
        if not title:
            raise ApplicationError("INVALID_SESSION_TITLE", "session title is empty")
        try:
            title = clean_session_title(self._scan_text(title))
            mutation = self._repository.rename_session(
                request.session_id,
                title,
                operation_id=request.operation_id,
            )
        except SensitiveContentDenied as error:
            raise ApplicationError("SENSITIVE_CONTENT_REJECTED", str(error)) from error
        except SensitiveScanError as error:
            raise ApplicationError("SENSITIVE_SCAN_FAILED", str(error)) from error
        except ResourceNotFoundError as error:
            raise ApplicationError("RESOURCE_NOT_FOUND", str(error)) from error
        except OperationConflictError as error:
            raise ApplicationError("OPERATION_ID_REUSED", str(error)) from error
        except OperationInProgressError as error:
            raise ApplicationError("OPERATION_IN_PROGRESS", str(error)) from error
        except ValueError as error:
            raise ApplicationError("INVALID_SESSION_TITLE", str(error)) from error
        return _result(SessionRenameResponseDto, session_to_legacy_dict(mutation.value))

    def delete(self, request: SessionDeleteRequestDto) -> SessionDeleteResponseDto:
        try:
            mutation = self._repository.delete_session(
                request.session_id,
                operation_id=request.operation_id,
            )
        except ResourceNotFoundError as error:
            raise ApplicationError("RESOURCE_NOT_FOUND", str(error)) from error
        except SessionActiveError as error:
            raise ApplicationError("SESSION_HAS_ACTIVE_RUN", str(error)) from error
        except OperationConflictError as error:
            raise ApplicationError("OPERATION_ID_REUSED", str(error)) from error
        except OperationInProgressError as error:
            raise ApplicationError("OPERATION_IN_PROGRESS", str(error)) from error
        return _result(
            SessionDeleteResponseDto,
            deleted_session_to_legacy_dict(mutation.value),
        )

    def list_events(self, request: EventListRequestDto) -> EventListResponseDto:
        try:
            events = self._store.list_events(
                request.session_id,
                after_event_id=request.after_event_id,
                limit=request.limit,
            )
        except ValueError as error:
            raise ApplicationInvalidParamsError("INVALID_EVENT_CURSOR", str(error)) from error
        return _result(EventListResponseDto, events)


def _result(result_type: type[ResultT], value: object) -> ResultT:
    try:
        return result_type.model_validate(value)
    except ValidationError as error:
        raise ApplicationError(
            "INTERNAL_ERROR", "stored Session result violates its protocol contract"
        ) from error


__all__ = [
    "MAX_SESSION_TITLE_BYTES",
    "SessionApplication",
    "SessionStorePort",
    "TypedSessionRepositoryPort",
    "clean_session_title",
]
