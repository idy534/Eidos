from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field, field_validator

from eidos_runtime.models import EidosFrozenStrictModel


class SessionTaskStatus(StrEnum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class Session(EidosFrozenStrictModel):
    id: str = Field(min_length=1)
    workspace_root: str = Field(min_length=1, max_length=4096)
    worktree_id: str | None = Field(default=None, min_length=1)
    title: str | None = None
    task_status: SessionTaskStatus
    created_at: datetime
    updated_at: datetime

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str | None:
        if value is not None and (
            not value
            or len(value) > 60
            or len(value.encode("utf-8")) > 120
        ):
            raise ValueError("session title is invalid")
        return value

    @field_validator("created_at", "updated_at")
    @classmethod
    def validate_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("session timestamp must be UTC")
        normalized = value.astimezone(UTC)
        if normalized.microsecond % 1_000:
            raise ValueError("session timestamp must use millisecond precision")
        return normalized


class SessionPage(EidosFrozenStrictModel):
    items: tuple[Session, ...]
    next_cursor: str | None = None


class DeletedSession(EidosFrozenStrictModel):
    deleted_session_id: str = Field(min_length=1)
