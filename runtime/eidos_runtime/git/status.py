from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field

from eidos_runtime.models import EidosFrozenStrictModel


class DiffScope(StrEnum):
    HEAD = "head"
    BASELINE = "baseline"


class GitStatusSnapshot(EidosFrozenStrictModel):
    worktree_id: str
    repository_root: str
    worktree_root: str
    base_ref: str
    base_commit: str
    branch: str
    head: str
    dirty: bool
    staged_count: int = Field(ge=0)
    unstaged_count: int = Field(ge=0)
    untracked_count: int = Field(ge=0)
    conflict_count: int = Field(ge=0)
    observed_at: datetime


class GitDiffSnapshot(EidosFrozenStrictModel):
    scope: DiffScope
    base_commit: str
    head: str
    dirty: bool
    changed_files: tuple[str, ...]
    unified_diff: str
    truncated: bool
    observed_at: datetime


def parse_porcelain_v2_status(output: str) -> tuple[int, int, int, int]:
    staged = 0
    unstaged = 0
    untracked = 0
    conflicts = 0
    for line in output.splitlines():
        if line.startswith("?"):
            untracked += 1
            continue
        if line.startswith("u "):
            conflicts += 1
        if line.startswith(("1 ", "2 ", "u ")):
            fields = line.split(" ", 2)
            if len(fields) < 2 or len(fields[1]) < 2:
                continue
            index_state, worktree_state = fields[1][0], fields[1][1]
            staged += int(index_state != "." and index_state != " ")
            unstaged += int(worktree_state != "." and worktree_state != " ")
    return staged, unstaged, untracked, conflicts


def utc_now() -> datetime:
    return datetime.fromtimestamp(
        int(datetime.now(UTC).timestamp() * 1000) / 1000,
        tz=UTC,
    )


__all__ = [
    "DiffScope",
    "GitDiffSnapshot",
    "GitStatusSnapshot",
    "parse_porcelain_v2_status",
    "utc_now",
]
