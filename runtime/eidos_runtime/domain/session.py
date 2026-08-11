from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from eidos_runtime.domain.worktree import WorktreeState
from eidos_runtime.models import EidosFrozenStrictModel


class SessionTaskStatus(StrEnum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class SessionExecutionMode(StrEnum):
    LOCAL = "local"
    WORKTREE = "worktree"


class Session(EidosFrozenStrictModel):
    id: str = Field(min_length=1)
    workspace_root: str = Field(min_length=1, max_length=4096)
    execution_mode: SessionExecutionMode = SessionExecutionMode.LOCAL
    worktree_id: str | None = Field(default=None, min_length=1)
    associated_worktree_id: str | None = Field(default=None, min_length=1)
    title: str | None = None
    task_status: SessionTaskStatus
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_execution_binding(self) -> "Session":
        if self.execution_mode is SessionExecutionMode.LOCAL and self.worktree_id is not None:
            raise ValueError("local Session must not have a Worktree binding")
        if self.execution_mode is SessionExecutionMode.WORKTREE and self.worktree_id is None:
            raise ValueError("worktree Session must have a Worktree binding")
        if (
            self.execution_mode is SessionExecutionMode.WORKTREE
            and self.associated_worktree_id != self.worktree_id
        ):
            raise ValueError(
                "worktree Session associated Worktree must match active binding"
            )
        return self

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


class SessionWorktreeProjection(EidosFrozenStrictModel):
    worktree_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    repository_root: str = Field(min_length=1, max_length=4096)
    worktree_root: str = Field(min_length=1, max_length=4096)
    base_ref: str = Field(min_length=1, max_length=4096)
    base_commit: str = Field(
        min_length=40,
        max_length=64,
        pattern=r"^[0-9a-fA-F]+$",
    )
    branch: str | None = Field(default=None, min_length=1, max_length=4096)
    state: WorktreeState


class SessionProjectProjection(EidosFrozenStrictModel):
    id: str = Field(min_length=1)
    workspace_root: str = Field(min_length=1, max_length=4096)
    git_available: bool


class SessionProjection(EidosFrozenStrictModel):
    session: Session
    project: SessionProjectProjection
    worktree: SessionWorktreeProjection | None = None

    @model_validator(mode="after")
    def validate_binding(self) -> "SessionProjection":
        if self.project.workspace_root != self.session.workspace_root:
            raise ValueError("Session Project projection is inconsistent")
        if self.session.execution_mode is SessionExecutionMode.LOCAL:
            if self.session.worktree_id is not None or self.worktree is not None:
                raise ValueError("local Session must not have a Worktree binding")
            # A pre-v18 direct Session can share a workspace with a Git-capable
            # Project. It remains a direct execution mode until the user creates
            # a new managed Session; without a Worktree it has no Git review.
            return self
        if self.session.worktree_id is None:
            raise ValueError("worktree Session must have a Worktree binding")
        if self.worktree is None or self.worktree.worktree_id != self.session.worktree_id:
            raise ValueError("managed Session Worktree projection is inconsistent")
        if self.worktree.project_id != self.project.id:
            raise ValueError("managed Session Project projection is inconsistent")
        if self.worktree.repository_root != self.session.workspace_root:
            raise ValueError("managed Session repository projection is inconsistent")
        if not self.project.git_available:
            raise ValueError("managed Session Project must expose Git")
        return self


class SessionProjectionPage(EidosFrozenStrictModel):
    items: tuple[SessionProjection, ...]
    next_cursor: str | None = None


class DeletedSession(EidosFrozenStrictModel):
    deleted_session_id: str = Field(min_length=1)
