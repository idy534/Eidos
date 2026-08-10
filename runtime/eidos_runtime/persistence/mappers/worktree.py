from __future__ import annotations

from datetime import UTC, datetime
from typing import Mapping

from eidos_runtime.domain.project import Project
from eidos_runtime.domain.worktree import Worktree, WorktreeOwnership, WorktreeState


def project_from_row(row: Mapping[str, object]) -> Project:
    return Project(
        id=str(row["id"]),
        repository_root=str(row["repository_root"]),
        git_common_dir=str(row["git_common_dir"]),
        created_at=_timestamp(int(row["created_at"])),
        updated_at=_timestamp(int(row["updated_at"])),
    )


def worktree_from_row(row: Mapping[str, object]) -> Worktree:
    return Worktree(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        worktree_root=str(row["worktree_root"]),
        git_dir=str(row["git_dir"]),
        base_ref=str(row["base_ref"]),
        base_commit=str(row["base_commit"]),
        branch=str(row["branch"]),
        ownership=WorktreeOwnership(str(row["ownership"])),
        state=WorktreeState(str(row["state"])),
        created_at=_timestamp(int(row["created_at"])),
        updated_at=_timestamp(int(row["updated_at"])),
    )


def _timestamp(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=UTC)


__all__ = ["project_from_row", "worktree_from_row"]
