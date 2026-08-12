from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field, field_validator

from eidos_runtime.models.base import EidosFrozenStrictModel
from eidos_runtime.domain.worktree import BranchOwnership


class WorktreeSnapshotState(StrEnum):
    READY = "ready"
    RESTORED = "restored"
    INVALID = "invalid"


class WorktreeSnapshot(EidosFrozenStrictModel):
    """Durable metadata for one disposable managed Worktree snapshot."""

    id: str = Field(min_length=1, max_length=256)
    worktree_id: str = Field(min_length=1)
    session_id: str | None = Field(default=None, min_length=1)
    project_id: str = Field(min_length=1)
    base_ref: str = Field(min_length=1, max_length=4096)
    base_commit: str = Field(min_length=40, max_length=64, pattern=r"^[0-9a-fA-F]+$")
    head: str = Field(min_length=40, max_length=64, pattern=r"^[0-9a-fA-F]+$")
    branch: str | None = Field(default=None, min_length=1, max_length=4096)
    checkout_branch: str | None = Field(default=None, min_length=1, max_length=4096)
    branch_ownership: BranchOwnership = BranchOwnership.NONE
    dirty: bool
    staged_paths: tuple[str, ...] = ()
    unstaged_paths: tuple[str, ...] = ()
    untracked_paths: tuple[str, ...] = ()
    conflict_paths: tuple[str, ...] = ()
    source_fingerprint: str = Field(min_length=1, max_length=256)
    artifact_path: str = Field(min_length=1, max_length=4096)
    artifact_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    full_patch_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    staged_patch_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    format_version: int = Field(default=1, ge=1, le=100)
    state: WorktreeSnapshotState = WorktreeSnapshotState.READY
    created_at: datetime
    restored_at: datetime | None = None
    updated_at: datetime

    @field_validator("created_at", "restored_at", "updated_at")
    @classmethod
    def validate_utc_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("snapshot timestamp must be UTC")
        normalized = value.astimezone(UTC)
        if normalized.microsecond % 1_000:
            raise ValueError("snapshot timestamp must use millisecond precision")
        return normalized


__all__ = ["WorktreeSnapshot", "WorktreeSnapshotState"]
