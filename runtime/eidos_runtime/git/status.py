from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field

from eidos_runtime.models import EidosFrozenStrictModel


class DiffScope(StrEnum):
    HEAD = "head"
    BASELINE = "baseline"


class GitStatusSnapshot(EidosFrozenStrictModel):
    worktree_id: str | None
    repository_root: str
    worktree_root: str
    base_ref: str | None
    base_commit: str | None
    branch: str | None
    head: str
    dirty: bool
    staged_count: int = Field(ge=0)
    unstaged_count: int = Field(ge=0)
    untracked_count: int = Field(ge=0)
    conflict_count: int = Field(ge=0)
    staged_files: tuple[str, ...]
    unstaged_files: tuple[str, ...]
    untracked_files: tuple[str, ...]
    conflict_files: tuple[str, ...]
    observed_at: datetime


class GitDiffSnapshot(EidosFrozenStrictModel):
    scope: DiffScope
    base_commit: str | None
    head: str
    dirty: bool
    changed_files: tuple[str, ...]
    unified_diff: str
    truncated: bool
    observed_at: datetime


def utc_now() -> datetime:
    return datetime.fromtimestamp(
        int(datetime.now(UTC).timestamp() * 1000) / 1000,
        tz=UTC,
    )


__all__ = [
    "DiffScope",
    "GitDiffSnapshot",
    "GitStatusSnapshot",
    "utc_now",
]
