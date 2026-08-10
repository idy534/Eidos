from __future__ import annotations

from collections.abc import Mapping

from pydantic import ValidationError

from eidos_runtime.domain.session import (
    DeletedSession,
    Session,
    SessionPage,
    SessionTaskStatus,
)
from eidos_runtime.persistence.conversion import (
    RowReader,
    RowValues,
    utc_datetime_from_millis,
    utc_datetime_to_millis,
)
from eidos_runtime.persistence.errors import PersistenceCorruptionError


def session_from_row(row: RowValues | Mapping[str, object]) -> Session:
    values = RowReader(row, record="session")
    # Read scalar columns in the persisted record's stable order.  Apart from
    # making corruption diagnostics deterministic, this ensures a partial row
    # reports its first missing column instead of a later enum conversion.
    session_id = values.text("id")
    workspace_root = values.text("workspace_root")
    worktree_id = (
        values.optional_text("worktree_id")
        if "worktree_id" in row.keys()
        else None
    )
    title = values.optional_text("title")
    created_at = utc_datetime_from_millis(
        values.value("created_at"),
        record="session",
        field="created_at",
    )
    updated_at = utc_datetime_from_millis(
        values.value("updated_at"),
        record="session",
        field="updated_at",
    )
    task_status_value = values.text("task_status")
    try:
        task_status = SessionTaskStatus(task_status_value)
    except ValueError:
        raise PersistenceCorruptionError(
            "persistence_value_invalid",
            record="session",
            field="task_status",
        ) from None
    try:
        return Session(
            id=session_id,
            workspace_root=workspace_root,
            worktree_id=worktree_id,
            title=title,
            task_status=task_status,
            created_at=created_at,
            updated_at=updated_at,
        )
    except ValidationError as error:
        field = _validation_field(error)
        raise PersistenceCorruptionError(
            "persistence_record_invalid",
            record="session",
            field=field,
        ) from None


def session_from_legacy_dict(value: object) -> Session:
    if not isinstance(value, Mapping):
        raise PersistenceCorruptionError(
            "persistence_record_invalid",
            record="session_operation_result",
        )
    reader = RowReader(value, record="session_operation_result")
    return session_from_row({
        "id": reader.text("id"),
        "workspace_root": reader.text("workspaceRoot"),
        "worktree_id": (
            reader.optional_text("worktreeId") if "worktreeId" in value else None
        ),
        "title": (
            reader.optional_text("title") if "title" in value else None
        ),
        "task_status": reader.text("taskStatus"),
        "created_at": reader.value("createdAt"),
        "updated_at": reader.value("updatedAt"),
    })


def session_to_legacy_dict(session: Session) -> dict[str, object]:
    value: dict[str, object] = {
        "id": session.id,
        "workspaceRoot": session.workspace_root,
        "taskStatus": session.task_status.value,
        "createdAt": utc_datetime_to_millis(session.created_at),
        "updatedAt": utc_datetime_to_millis(session.updated_at),
    }
    if session.title is not None:
        value["title"] = session.title
    return value


def session_to_operation_dict(session: Session) -> dict[str, object]:
    """Keep the managed binding in internal operation replay state only."""

    value = session_to_legacy_dict(session)
    if session.worktree_id is not None:
        value["worktreeId"] = session.worktree_id
    return value


def session_page_to_legacy_dict(page: SessionPage) -> dict[str, object]:
    value: dict[str, object] = {
        "items": [session_to_legacy_dict(session) for session in page.items]
    }
    if page.next_cursor is not None:
        value["nextCursor"] = page.next_cursor
    return value


def deleted_session_from_legacy_dict(value: object) -> DeletedSession:
    if not isinstance(value, Mapping):
        raise PersistenceCorruptionError(
            "persistence_record_invalid",
            record="deleted_session_operation_result",
        )
    reader = RowReader(value, record="deleted_session_operation_result")
    try:
        return DeletedSession(
            deleted_session_id=reader.text("deletedSessionId")
        )
    except ValidationError:
        raise PersistenceCorruptionError(
            "persistence_record_invalid",
            record="deleted_session_operation_result",
            field="deletedSessionId",
        ) from None


def deleted_session_to_legacy_dict(value: DeletedSession) -> dict[str, object]:
    return {"deletedSessionId": value.deleted_session_id}


def _validation_field(error: ValidationError) -> str | None:
    location = error.errors(include_url=False)[0].get("loc", ())
    return str(location[0]) if location else None
