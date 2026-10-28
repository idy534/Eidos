from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import json

from eidos_runtime.domain.project_workspace import SessionRoot
import sqlite3
import stat

from eidos_runtime.db.database import WorkspaceIdentity
from eidos_runtime.db.errors import ResourceNotFoundError, StorageError


_SESSION_WORKSPACE_SELECT = """
    SELECT s.id, s.workspace_root, s.workspace_dev, s.workspace_inode, s.workspace_roots_json,
           s.workspace_uid, s.worktree_id, s.execution_mode,
           w.worktree_root, w.state, w.git_dir, w.repository_root, w.git_common_dir AS worktree_common_dir,
           p.workspace_root AS project_workspace_root, p.git_common_dir
    FROM sessions s
    LEFT JOIN worktrees w ON w.id = s.worktree_id
    LEFT JOIN projects p ON p.id = w.project_id
    WHERE s.id = ?
"""


def execution_workspace_for_session(
    connection: sqlite3.Connection, session_id: str
) -> WorkspaceIdentity:
    row = connection.execute(_SESSION_WORKSPACE_SELECT, (session_id,)).fetchone()
    if row is None:
        raise ResourceNotFoundError("session not found")
    return _with_roots(row, _identity_from_session_row(row))


def execution_workspace_for_run(
    connection: sqlite3.Connection, run_id: str
) -> WorkspaceIdentity:
    row = connection.execute(
        _SESSION_WORKSPACE_SELECT.replace(
            "WHERE s.id = ?", "JOIN runs r ON r.session_id = s.id WHERE r.id = ?"
        ),
        (run_id,),
    ).fetchone()
    if row is None:
        raise ResourceNotFoundError("run not found")
    return _with_roots(row, _identity_from_session_row(row))


def _identity_from_session_row(row: sqlite3.Row) -> WorkspaceIdentity:
    execution_mode = row["execution_mode"]
    if execution_mode is None:
        execution_mode = "worktree" if row["worktree_id"] is not None else "local"
    if execution_mode == "local":
        if row["worktree_id"] is not None:
            raise StorageError("local session has a worktree binding")
        if any(
            row[field] is None
            for field in ("workspace_dev", "workspace_inode", "workspace_uid")
        ):
            raise StorageError("workspace identity is unavailable")
        return WorkspaceIdentity(
            path=Path(row["workspace_root"]),
            device=row["workspace_dev"],
            inode=row["workspace_inode"],
            owner=row["workspace_uid"],
        )

    if execution_mode != "worktree" or row["worktree_id"] is None:
        raise StorageError("session execution binding is invalid")
    if (
        row["worktree_root"] is None
        or row["workspace_root"] is None
    ):
        raise StorageError("session worktree repository mismatch")
    if row["state"] != "active":
        raise StorageError("session worktree is unavailable")

    root = Path(row["worktree_root"])
    try:
        if root.is_symlink():
            raise StorageError("session worktree is a symlink")
        resolved = root.resolve(strict=True)
        metadata = resolved.stat()
    except StorageError:
        raise
    except OSError as error:
        raise StorageError("session worktree is unavailable") from error
    if not stat.S_ISDIR(metadata.st_mode):
        raise StorageError("session worktree is unavailable")
    return WorkspaceIdentity(
        path=resolved,
        device=metadata.st_dev,
        inode=metadata.st_ino,
        owner=metadata.st_uid,
        git_dir=(
            Path(row["git_dir"]).resolve(strict=False)
            if row["git_dir"] is not None
            else None
        ),
        git_common_dir=(
            Path(row["worktree_common_dir"] or row["git_common_dir"]).resolve(strict=False)
            if (row["worktree_common_dir"] or row["git_common_dir"]) is not None
            else None
        ),
    )



def _with_roots(row: sqlite3.Row, primary: WorkspaceIdentity) -> WorkspaceIdentity:
    from eidos_runtime.workspace.reader import capture_workspace_identity
    roots = tuple(SessionRoot.model_validate(value) for value in json.loads(row["workspace_roots_json"]))
    if not roots:
        return primary
    identities = []
    for root in roots:
        if row["worktree_root"] is not None and row["execution_mode"] == "worktree" and root.repository_root == row["repository_root"]:
            relative = Path(root.path).relative_to(root.repository_root)
            identity = capture_workspace_identity(Path(row["worktree_root"]) / relative)
            identity = replace(identity, git_dir=primary.git_dir, git_common_dir=primary.git_common_dir)
        else:
            identity = WorkspaceIdentity(
                path=Path(root.path), device=root.device, inode=root.inode, owner=root.owner,
                git_dir=Path(root.git_dir) if root.git_dir else None,
                git_common_dir=Path(root.git_common_dir) if root.git_common_dir else None,
            )
        identities.append(replace(identity, root_id=root.id))
    return replace(identities[0], additional_roots=tuple(identities[1:]))


__all__ = [
    "execution_workspace_for_run",
    "execution_workspace_for_session",
]
