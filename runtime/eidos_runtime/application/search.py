from __future__ import annotations

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.db.errors import InvalidCursorError, ResourceNotFoundError
from eidos_runtime.db.repositories.sessions import SessionRepository
from eidos_runtime.persistence.search import SearchRepository
from eidos_runtime.protocol.methods import SessionReadResponseDto
from eidos_runtime.protocol.search import (
    ReadAroundRequest,
    SearchRequest,
    SearchResponse,
)


class SearchApplication:
    def __init__(
        self, repository: SearchRepository, sessions: SessionRepository
    ) -> None:
        self.repository = repository
        self.sessions = sessions

    def search(self, request: SearchRequest) -> SearchResponse:
        try:
            return self.repository.search(request)
        except InvalidCursorError as error:
            raise ApplicationError("INVALID_PARAMS", str(error)) from error
        except ResourceNotFoundError as error:
            raise ApplicationError("RESOURCE_NOT_FOUND", str(error)) from error

    def read_around(self, request: ReadAroundRequest) -> SessionReadResponseDto:
        try:
            cursor = self.repository.around_cursor(request.session_id, request.item_id)
            return SessionReadResponseDto.model_validate(
                self.sessions.read_session_snapshot(
                    request.session_id, item_limit=40, before_item_id=cursor
                )
            )
        except ResourceNotFoundError as error:
            raise ApplicationError("RESOURCE_NOT_FOUND", str(error)) from error
