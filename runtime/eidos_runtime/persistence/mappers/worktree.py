from __future__ import annotations

from datetime import UTC, datetime
from typing import Mapping

from eidos_runtime.domain.project import Project
from eidos_runtime.domain.worktree import (
    BranchOwnership,
    Worktree,
    WorktreeOwnership,
    WorktreeState,
)


def project_from_row(row: Mapping[str, object]) -> Project:
    return Project(
        id=str(row["id"]),
        workspace_root=str(row["workspace_root"]),
        git_repository_root=(
            str(row["git_repository_root"])
            if row["git_repository_root"] is not None
            else None
        ),
        git_common_dir=(
            str(row["git_common_dir"])
            if row["git_common_dir"] is not None
            else None
        ),
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
        branch=(str(row["branch"]) if row["branch"] is not None else None),
        checkout_branch=(
            str(row["checkout_branch"])
            if "checkout_branch" in row.keys()
            and row["checkout_branch"] is not None
            else None
        ),
        branch_ownership=BranchOwnership(str(row["branch_ownership"])),
        ownership=WorktreeOwnership(str(row["ownership"])),
        state=WorktreeState(str(row["state"])),
        created_at=_timestamp(int(row["created_at"])),
        updated_at=_timestamp(int(row["updated_at"])),
        last_used_at=_timestamp(
            int(row["last_used_at"])
            if "last_used_at" in row.keys() and row["last_used_at"] is not None
            else int(row["updated_at"])
        ),
    )


def _timestamp(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=UTC)


__all__ = ["project_from_row", "worktree_from_row"]
