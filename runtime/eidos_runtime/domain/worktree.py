from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from eidos_runtime.models import EidosFrozenStrictModel


class WorktreeOwnership(StrEnum):
    MANAGED = "managed"
    ADOPTED = "adopted"


class BranchOwnership(StrEnum):
    NONE = "none"
    LEGACY_MANAGED = "legacy_managed"
    USER = "user"


class WorktreeState(StrEnum):
    ACTIVE = "active"
    MISSING = "missing"
    INVALID = "invalid"
    DELETED = "deleted"


class WorktreeLifecycleScope(StrEnum):
    SESSION_CREATE = "session/create"
    SESSION_DELETE = "session/delete"
    CHECKPOINT_FORK = "checkpoint/fork"
    ATTACH_BRANCH = "worktree/attach-branch"


class WorktreeLifecycleState(StrEnum):
    PREPARED = "prepared"
    WORKTREE_CREATED = "worktree_created"
    SESSION_CREATED = "session_created"
    RUN_CREATED = "run_created"
    CHECKPOINT_ACTION_CREATED = "checkpoint_action_created"
    BRANCH_ATTACHED = "branch_attached"
    WORKTREE_DELETED = "worktree_deleted"
    COMPLETED = "completed"
    CLEANUP_REQUIRED = "cleanup_required"


class Worktree(EidosFrozenStrictModel):
    """Durable Worktree facts. HEAD and dirty state are deliberately absent."""

    id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    worktree_root: str = Field(min_length=1, max_length=4096)
    git_dir: str = Field(min_length=1, max_length=4096)
    base_ref: str = Field(min_length=1, max_length=4096)
    base_commit: str = Field(
        min_length=40,
        max_length=64,
        pattern=r"^[0-9a-fA-F]+$",
    )
    branch: str | None = Field(default=None, min_length=1, max_length=4096)
    branch_ownership: BranchOwnership = BranchOwnership.NONE
    ownership: WorktreeOwnership
    state: WorktreeState
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def infer_legacy_branch_ownership(cls, value: object) -> object:
        if not isinstance(value, dict) or "branch_ownership" in value:
            return value
        candidate = dict(value)
        candidate["branch_ownership"] = (
            BranchOwnership.NONE
            if candidate.get("branch") is None
            else BranchOwnership.LEGACY_MANAGED
        )
        return candidate

    @model_validator(mode="after")
    def validate_branch_ownership(self) -> "Worktree":
        if self.branch is None and self.branch_ownership is not BranchOwnership.NONE:
            raise ValueError("detached Worktree cannot own a branch")
        if self.branch is not None and self.branch_ownership is BranchOwnership.NONE:
            raise ValueError("attached Worktree must declare branch ownership")
        return self

    @field_validator("worktree_root", "git_dir")
    @classmethod
    def validate_absolute_path(cls, value: str) -> str:
        if "\x00" in value or not value.startswith("/"):
            raise ValueError("worktree path must be absolute")
        return value

    @field_validator("created_at", "updated_at")
    @classmethod
    def validate_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("worktree timestamp must be UTC")
        normalized = value.astimezone(UTC)
        if normalized.microsecond % 1_000:
            raise ValueError("worktree timestamp must use millisecond precision")
        return normalized


class WorktreeValidation(EidosFrozenStrictModel):
    """The result of comparing one durable Worktree with Git and the filesystem."""

    worktree: Worktree
    valid: bool
    code: str | None = None
    head: str | None = None
    observed_worktree_root: str | None = None
    observed_git_dir: str | None = None
    observed_git_common_dir: str | None = None
    observed_branch: str | None = None


class WorktreeView(EidosFrozenStrictModel):
    """One persisted Worktree joined with its current Git observation."""

    worktree: Worktree
    actual_present: bool
    head: str | None = None
    branch: str | None = None
    dirty: bool | None = None


class OrphanWorktreeCandidate(EidosFrozenStrictModel):
    """A Git worktree that has no matching Runtime record."""

    project_id: str
    worktree_root: str
    git_dir: str
    branch: str | None = None
    head: str | None = None


class WorktreeRecoveryReport(EidosFrozenStrictModel):
    updated_worktrees: tuple[Worktree, ...] = ()
    orphan_candidates: tuple[OrphanWorktreeCandidate, ...] = ()


class WorktreeLifecycleOperation(EidosFrozenStrictModel):
    scope: WorktreeLifecycleScope
    operation_id: str = Field(min_length=1)
    state: WorktreeLifecycleState
    project_id: str | None = None
    repository_root: str | None = None
    worktree_id: str | None = None
    worktree_root: str | None = None
    base_ref: str | None = None
    branch: str | None = None
    base_commit: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    checkpoint_id: str | None = None
    include_local_changes: bool = False
    source_head: str | None = None
    source_branch: str | None = None
    source_dirty: bool | None = None
    source_fingerprint: str | None = None
    error_code: str | None = None
    created_at: datetime
    updated_at: datetime


class WorktreeCleanupReport(EidosFrozenStrictModel):
    pruned_project_ids: tuple[str, ...] = ()
    marked_deleted: tuple[Worktree, ...] = ()


__all__ = [
    "BranchOwnership",
    "OrphanWorktreeCandidate",
    "Worktree",
    "WorktreeCleanupReport",
    "WorktreeLifecycleOperation",
    "WorktreeLifecycleScope",
    "WorktreeLifecycleState",
    "WorktreeOwnership",
    "WorktreeRecoveryReport",
    "WorktreeState",
    "WorktreeValidation",
    "WorktreeView",
]
