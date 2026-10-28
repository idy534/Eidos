from __future__ import annotations

import json
import sqlite3
import uuid

from eidos_runtime.db.database import CommittedMutation, Repository, now_ms
from eidos_runtime.db.errors import ResourceNotFoundError, StorageError
from eidos_runtime.db.events import append_event
from eidos_runtime.domain.project import Project, default_project_name
from eidos_runtime.domain.project_workspace import ProjectRoot, SessionRoot
from eidos_runtime.persistence.mappers.worktree import project_from_row
from eidos_runtime.runtime.state_machine import EventType


class ProjectConfigurationConflict(StorageError):
    pass


def project_roots(connection: sqlite3.Connection, project_id: str) -> tuple[ProjectRoot, ...]:
    rows = connection.execute(
        "SELECT id,path,repository_root,git_dir,git_common_dir FROM project_roots "
        "WHERE project_id=? ORDER BY position", (project_id,),
    ).fetchall()
    return tuple(ProjectRoot.model_validate(dict(row)) for row in rows)


def complete_project(connection: sqlite3.Connection, row: sqlite3.Row) -> Project:
    project = project_from_row(row)
    return project.model_copy(update={
        "roots": project_roots(connection, project.id),
        "primary_root_id": row["primary_root_id"], "revision": row["revision"],
    })


def ensure_project_root(
    connection: sqlite3.Connection, project: Project, *, git_dir: str | None = None,
) -> Project:
    if not project_roots(connection, project.id):
        identifier = "root_" + uuid.uuid4().hex
        connection.execute(
            "INSERT INTO project_roots(id,project_id,path,position,repository_root,git_dir,git_common_dir) "
            "VALUES(?,?,?,0,?,?,?)",
            (identifier, project.id, project.workspace_root, project.git_repository_root,
             git_dir, project.git_common_dir),
        )
        connection.execute("UPDATE projects SET primary_root_id=? WHERE id=?", (identifier, project.id))
    row = connection.execute("SELECT * FROM projects WHERE id=?", (project.id,)).fetchone()
    return complete_project(connection, row)


def capture_session_roots(roots: tuple[ProjectRoot, ...]) -> tuple[SessionRoot, ...]:
    from eidos_runtime.workspace.reader import capture_workspace_identity
    result = []
    for root in roots:
        identity = capture_workspace_identity(root.path)
        result.append(SessionRoot(
            **root.model_dump(), device=identity.device, inode=identity.inode, owner=identity.owner,
        ))
    return tuple(result)


class ProjectWorkspaceRepository(Repository):
    def create(
        self, *, name: str | None, roots: tuple[ProjectRoot, ...],
        primary_root_id: str, operation_id: str,
    ) -> CommittedMutation[Project]:
        primary = next((root for root in roots if root.id == primary_root_id), None)
        if primary is None or len({root.path for root in roots}) != len(roots) or len({root.id for root in roots}) != len(roots):
            raise ValueError("project folders must be distinct and include one primary folder")

        def write(connection: sqlite3.Connection) -> CommittedMutation[Project]:
            identifier = "project_" + uuid.uuid4().hex
            now = now_ms()
            connection.execute(
                "INSERT INTO projects(id,name,workspace_root,git_repository_root,git_common_dir,created_at,updated_at,primary_root_id) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (identifier, (name or default_project_name(primary.path)).strip(), primary.path,
                 primary.repository_root, primary.git_common_dir, now, now, primary_root_id),
            )
            for position, root in enumerate(roots):
                connection.execute(
                    "INSERT INTO project_roots(id,project_id,path,position,repository_root,git_dir,git_common_dir) VALUES(?,?,?,?,?,?,?)",
                    (root.id, identifier, root.path, position, root.repository_root, root.git_dir, root.git_common_dir),
                )
            project = complete_project(connection, connection.execute("SELECT * FROM projects WHERE id=?", (identifier,)).fetchone())
            return CommittedMutation(project, (append_event(connection, EventType.PROJECT_UPDATED, now, {"id": identifier}),))

        return self._write_committed(
            write, operation_id=operation_id, operation_scope="project/createFolders",
            operation_request={"name": name, "roots": [root.model_dump(mode="json") for root in roots], "primaryRootId": primary_root_id},
            serialize_value=lambda value: value.model_dump(mode="json"), deserialize_value=lambda value: Project.model_validate_json(json.dumps(value)),
        )

    def read(self, project_id: str) -> Project:
        with self.lock:
            connection = self._connection()
            row = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if row is None:
                raise ResourceNotFoundError("project not found")
            return complete_project(connection, row)

    def update(
        self, project_id: str, *, expected_revision: int, name: str | None,
        roots: tuple[ProjectRoot, ...], primary_root_id: str, operation_id: str,
    ) -> CommittedMutation[Project]:
        primary = next((root for root in roots if root.id == primary_root_id), None)
        if primary is None or not roots or len({root.path for root in roots}) != len(roots):
            raise ValueError("project folders must be distinct and include the primary folder")
        if len({root.id for root in roots}) != len(roots):
            raise ValueError("project folder IDs must be distinct")
        request = {
            "projectId": project_id, "expectedRevision": expected_revision, "name": name,
            "roots": [root.model_dump(mode="json") for root in roots], "primaryRootId": primary_root_id,
        }

        def write(connection: sqlite3.Connection) -> CommittedMutation[Project]:
            row = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if row is None:
                raise ResourceNotFoundError("project not found")
            if row["revision"] != expected_revision:
                raise ProjectConfigurationConflict("project configuration changed")
            old_roots = {root.id: root.path for root in project_roots(connection, project_id)}
            if any(root.id in old_roots and old_roots[root.id] != root.path for root in roots):
                raise ValueError("an existing folder ID cannot be rebound")
            now = now_ms()
            connection.execute(
                "UPDATE projects SET name=?,workspace_root=?,git_repository_root=?,git_common_dir=?,"
                "primary_root_id=?,revision=revision+1,updated_at=? WHERE id=?",
                ((name or default_project_name(primary.path)).strip(), primary.path,
                 primary.repository_root, primary.git_common_dir, primary_root_id, now, project_id),
            )
            connection.execute("DELETE FROM project_roots WHERE project_id=?", (project_id,))
            for position, root in enumerate(roots):
                connection.execute(
                    "INSERT INTO project_roots(id,project_id,path,position,repository_root,git_dir,git_common_dir) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (root.id, project_id, root.path, position, root.repository_root,
                     root.git_dir, root.git_common_dir),
                )
            updated = complete_project(connection, connection.execute(
                "SELECT * FROM projects WHERE id=?", (project_id,),
            ).fetchone())
            event = append_event(connection, EventType.PROJECT_UPDATED, now, {"id": project_id})
            return CommittedMutation(updated, (event,))

        return self._write_committed(
            write, operation_id=operation_id, operation_scope="project/update", operation_request=request,
            serialize_value=lambda value: value.model_dump(mode="json"),
            deserialize_value=lambda value: Project.model_validate_json(json.dumps(value)),
        )

    def bind_session(self, session_id: str, project_id: str) -> None:
        def write(connection: sqlite3.Connection) -> None:
            project = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if project is None:
                raise ResourceNotFoundError("project not found")
            roots = capture_session_roots(project_roots(connection, project_id))
            primary = project["primary_root_id"]
            roots = tuple(sorted(roots, key=lambda root: root.id != primary))
            connection.execute(
                "UPDATE sessions SET project_id=?,workspace_roots_json=? WHERE id=?",
                (project_id, json.dumps([root.model_dump(mode="json") for root in roots]), session_id),
            )
        self._write(write)



def project_for_session(project: Project, session) -> Project:
    root = session.roots[0] if session.roots else None
    if root is None:
        return project
    return project.model_copy(update={
        "workspace_root": session.workspace_root,
        "git_repository_root": root.repository_root,
        "git_common_dir": root.git_common_dir,
    })
