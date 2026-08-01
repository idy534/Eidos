from __future__ import annotations

from eidos_runtime.db.repositories.sessions import (
    DEFAULT_LIST_LIMIT,
    SessionRepository,
)
from eidos_runtime.domain.session import DeletedSession, Session, SessionPage


class SessionApplication:
    """Coordinates Session use cases over the typed repository port."""

    def __init__(self, repository: SessionRepository) -> None:
        self.repository = repository

    def create(self, workspace_root: str, *, operation_id: str | None = None) -> Session:
        return self.repository.create_session(
            workspace_root, operation_id=operation_id
        )

    def list(
        self, *, limit: int = DEFAULT_LIST_LIMIT, cursor: str | None = None
    ) -> SessionPage:
        return self.repository.list_sessions(limit=limit, cursor=cursor)

    def read(self, session_id: str) -> Session | None:
        return self.repository.read_session(session_id)

    def rename(self, session_id: str, title: str) -> Session:
        return self.repository.rename_session(session_id, title)

    def delete(self, session_id: str) -> DeletedSession:
        return self.repository.delete_session(session_id)
