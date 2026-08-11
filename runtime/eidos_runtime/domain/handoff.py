from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, model_validator

from eidos_runtime.domain.session import SessionExecutionMode
from eidos_runtime.models import EidosFrozenStrictModel


class SessionHandoffScope(StrEnum):
    LOCAL = "session/handoff-local"
    WORKTREE = "session/handoff-worktree"


class SessionHandoffState(StrEnum):
    PREPARED = "prepared"
    SOURCE_CAPTURED = "source_captured"
    TARGET_MATERIALIZED = "target_materialized"
    SESSION_REBOUND = "session_rebound"
    COMPLETED = "completed"
    CLEANUP_REQUIRED = "cleanup_required"


class HandoffPlan(EidosFrozenStrictModel):
    """Immutable Git and Session facts used by one handoff attempt."""

    operation_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    source_mode: SessionExecutionMode
    target_mode: SessionExecutionMode
    source_root: str = Field(min_length=1, max_length=4096)
    target_root: str = Field(min_length=1, max_length=4096)
    source_common_dir: str = Field(min_length=1, max_length=4096)
    target_common_dir: str = Field(min_length=1, max_length=4096)
    associated_worktree_id: str = Field(min_length=1)
    target_worktree_new: bool
    target_base_ref: str | None = None
    target_base_commit: str | None = None
    source_head: str = Field(min_length=40, max_length=64)
    source_branch: str | None = None
    source_dirty: bool
    source_fingerprint: str = Field(min_length=64, max_length=64)
    target_head: str = Field(min_length=40, max_length=64)
    target_branch: str | None = None
    target_dirty: bool
    target_fingerprint: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def validate_modes(self) -> "HandoffPlan":
        if self.source_mode is self.target_mode:
            raise ValueError("handoff source and target modes must differ")
        if self.source_common_dir != self.target_common_dir:
            raise ValueError("handoff source and target repositories differ")
        return self


class SessionHandoffOperation(EidosFrozenStrictModel):
    scope: SessionHandoffScope
    operation_id: str = Field(min_length=1, max_length=128)
    state: SessionHandoffState
    session_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    source_mode: SessionExecutionMode
    target_mode: SessionExecutionMode
    source_root: str = Field(min_length=1, max_length=4096)
    target_root: str = Field(min_length=1, max_length=4096)
    source_common_dir: str = Field(min_length=1, max_length=4096)
    target_common_dir: str = Field(min_length=1, max_length=4096)
    associated_worktree_id: str = Field(min_length=1)
    target_worktree_new: bool
    target_base_ref: str | None = None
    target_base_commit: str | None = None
    source_head: str = Field(min_length=40, max_length=64)
    source_branch: str | None = None
    source_dirty: bool
    source_fingerprint: str = Field(min_length=64, max_length=64)
    target_head: str = Field(min_length=40, max_length=64)
    target_branch: str | None = None
    target_dirty: bool
    target_fingerprint: str = Field(min_length=64, max_length=64)
    target_after_head: str | None = None
    target_after_branch: str | None = None
    target_after_fingerprint: str | None = None
    source_after_head: str | None = None
    source_after_branch: str | None = None
    source_after_fingerprint: str | None = None
    error_code: str | None = None
    created_at: datetime
    updated_at: datetime

    @property
    def plan(self) -> HandoffPlan:
        return HandoffPlan(
            operation_id=self.operation_id,
            session_id=self.session_id,
            project_id=self.project_id,
            source_mode=self.source_mode,
            target_mode=self.target_mode,
            source_root=self.source_root,
            target_root=self.target_root,
            source_common_dir=self.source_common_dir,
            target_common_dir=self.target_common_dir,
            associated_worktree_id=self.associated_worktree_id,
            target_worktree_new=self.target_worktree_new,
            target_base_ref=self.target_base_ref,
            target_base_commit=self.target_base_commit,
            source_head=self.source_head,
            source_branch=self.source_branch,
            source_dirty=self.source_dirty,
            source_fingerprint=self.source_fingerprint,
            target_head=self.target_head,
            target_branch=self.target_branch,
            target_dirty=self.target_dirty,
            target_fingerprint=self.target_fingerprint,
        )


__all__ = [
    "HandoffPlan",
    "SessionHandoffOperation",
    "SessionHandoffScope",
    "SessionHandoffState",
]
