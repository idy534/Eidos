from __future__ import annotations

from typing import Mapping

from eidos_runtime.domain.project import Project, default_project_name
from eidos_runtime.domain.worktree import (
    BranchOwnership,
    Worktree,
    WorktreeOwnership,
    WorktreeState,
)
from eidos_runtime.persistence.codec import utc_datetime_from_millis


def project_from_row(row: Mapping[str, object]) -> Project:
    return Project(
        id=str(row["id"]),
        name=(
            str(row["name"])
            if "name" in row.keys() and row["name"]
            else default_project_name(str(row["workspace_root"]))
        ),
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
        created_at=utc_datetime_from_millis(int(row["created_at"])),
        updated_at=utc_datetime_from_millis(int(row["updated_at"])),
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
        created_at=utc_datetime_from_millis(int(row["created_at"])),
        updated_at=utc_datetime_from_millis(int(row["updated_at"])),
        last_used_at=utc_datetime_from_millis(
            int(row["last_used_at"])
            if "last_used_at" in row.keys() and row["last_used_at"] is not None
            else int(row["updated_at"])
        ),
    )
__all__ = ["project_from_row", "worktree_from_row"]
