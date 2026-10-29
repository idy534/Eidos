from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid
from typing import Literal

from eidos_runtime.db.database import (
    CommittedMutation,
    Repository,
    canonical_hash,
    now_ms as _now_ms,
)
from eidos_runtime.db.errors import (
    InvalidCursorError,
    ResourceNotFoundError,
    SessionActiveError,
    WorkspaceBoundaryError,
)
from eidos_runtime.db.events import append_event, event_from_row
from eidos_runtime.db.mappers import (
    _json_bytes,
    _run_from_row,
    _snapshot_item,
)
from eidos_runtime.file_limits import TOOL_TEXT_PAGE_CHARACTERS
from eidos_runtime.models.tool_text import ToolTextPage
from eidos_runtime.domain.project_workspace import SessionRoot
from eidos_runtime.domain.session import (
    DeletedSession,
    Session,
    SessionExecutionMode,
    SessionPage,
    SessionProjection,
    SessionProjectionPage,
)
from eidos_runtime.domain.project import default_project_name, direct_project_id
from eidos_runtime.persistence.mappers.session import (
    deleted_session_from_legacy_dict,
    deleted_session_to_legacy_dict,
    session_from_legacy_dict,
    session_from_row,
    session_projection_from_row,
    session_to_legacy_dict,
    session_to_operation_dict,
)
from eidos_runtime.runtime.state_machine import EventType
from eidos_runtime.workspace.projectless import projectless_root

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200
SESSION_CURSOR_PREFIX = "session-v2:"
MAX_SNAPSHOT_BYTES = 768 * 1024

SESSION_SELECT = """
    SELECT s.creation_seq, s.id, s.workspace_root, s.worktree_id, s.project_id, s.workspace_roots_json,
           s.associated_worktree_id,
           s.execution_mode, s.title,
           (SELECT source_json FROM session_forks WHERE session_id=s.id) AS fork_source_json,
           CASE WHEN automation_creation.id IS NOT NULL THEN automation_source.origin_json END AS automation_origin_json,
           automation_creation.config_json AS automation_config_json,
           s.created_at, s.updated_at,
           CASE
             WHEN EXISTS (
               SELECT 1 FROM runs active
               WHERE active.session_id = s.id
                 AND active.status IN (
                   'queued', 'running', 'waiting_approval', 'waiting_input', 'waiting_agents', 'finalizing'
                 )
             ) THEN 'in_progress'
             ELSE COALESCE((
               SELECT CASE latest.status
                 WHEN 'succeeded' THEN 'completed'
                 WHEN 'failed' THEN 'failed'
                 WHEN 'stopped' THEN 'failed'
                 WHEN 'interrupted' THEN 'failed'
                 WHEN 'canceled' THEN 'canceled'
                 ELSE 'new'
               END
               FROM runs latest
               WHERE latest.session_id = s.id
               ORDER BY latest.creation_seq DESC
               LIMIT 1
             ), 'new')
           END AS task_status,
           (SELECT active.status FROM runs active
            WHERE active.session_id = s.id
              AND active.status IN ('queued', 'running', 'waiting_approval', 'waiting_input', 'waiting_agents', 'finalizing')
            ORDER BY CASE active.status WHEN 'waiting_approval' THEN 0 ELSE 1 END,
                     active.creation_seq DESC LIMIT 1) AS active_run_status,
           COALESCE(p.id, direct_p.id) AS projection_project_id,
           s.workspace_root AS projection_workspace_root,
           CASE WHEN json_array_length(s.workspace_roots_json)>0
                THEN json_extract(s.workspace_roots_json,'$[0].repository_root') IS NOT NULL
                ELSE COALESCE(w.repository_root,p.git_repository_root,direct_p.git_repository_root) IS NOT NULL
           END AS projection_git_available,
           w.id AS projection_worktree_id,
           COALESCE(w.repository_root,p.git_repository_root,p.workspace_root) AS projection_repository_root,
           w.worktree_root AS projection_worktree_root,
           w.base_ref AS projection_base_ref,
           w.base_commit AS projection_base_commit,
           w.checkout_branch AS projection_branch,
           w.state AS projection_worktree_state
    FROM sessions s
    LEFT JOIN worktrees w ON w.id = s.worktree_id
    LEFT JOIN projects p ON p.id = w.project_id
    LEFT JOIN projects direct_p ON direct_p.id = s.project_id OR (s.project_id IS NULL AND direct_p.workspace_root = s.workspace_root)
    LEFT JOIN automation_run_origins automation_source ON automation_source.run_id = (
        SELECT r.id FROM runs r JOIN automation_run_origins a ON a.run_id=r.id
        WHERE r.session_id=s.id ORDER BY r.creation_seq LIMIT 1
    )
    LEFT JOIN scheduled_occurrences automation_creation
        ON automation_creation.id=json_extract(automation_source.origin_json,'$.occurrenceId')
        AND automation_creation.session_id=s.id
        AND json_extract(automation_creation.config_json,'$.target.kind')='new_session'
"""

class SessionRepository(Repository):
    def create_session(
        self,
        workspace_root: str,
        *,
        worktree_id: str | None = None,
        execution_mode: SessionExecutionMode = SessionExecutionMode.LOCAL,
        project_id: str | None = None,
        workspace_roots: tuple[SessionRoot, ...] | None = None,
        operation_id: str | None = None,
        operation_request: dict[str, object] | None = None,
        session_id: str | None = None,
        associated_worktree_id: str | None = None,
        projectless: bool = False,
    ) -> Session:
        return self.create_session_committed(
            workspace_root,
            worktree_id=worktree_id,
            execution_mode=execution_mode,
            project_id=project_id,
            workspace_roots=workspace_roots,
            operation_id=operation_id,
            operation_request=operation_request,
            session_id=session_id,
            associated_worktree_id=associated_worktree_id,
            projectless=projectless,
        ).value

    def create_session_committed(
        self,
        workspace_root: str,
        *,
        worktree_id: str | None = None,
        execution_mode: SessionExecutionMode = SessionExecutionMode.LOCAL,
        project_id: str | None = None,
        workspace_roots: tuple[SessionRoot, ...] | None = None,
        operation_id: str | None = None,
        operation_request: dict[str, object] | None = None,
        session_id: str | None = None,
        associated_worktree_id: str | None = None,
        projectless: bool = False,
        projectless_creation_id: str | None = None,
        title: str | None = None,
        transaction_connection: sqlite3.Connection | None = None,
    ) -> CommittedMutation[Session]:
        workspace = _canonical_workspace(workspace_root)
        if projectless and workspace.parent.parent != projectless_root():
            raise WorkspaceBoundaryError("projectless workspace is outside its managed root")
        if self._workspace_overlaps_data(
            workspace,
        ):
            raise WorkspaceBoundaryError("workspace overlaps runtime data")
        metadata = workspace.stat()
        session_id = session_id or str(uuid.uuid4())
        now = _now_ms()
        if title is not None and (not title or len(title) > 60 or len(title.encode("utf-8")) > 120):
            raise ValueError("session title is invalid")
        try:
            execution_mode = SessionExecutionMode(execution_mode)
        except ValueError as error:
            raise ValueError("invalid session execution mode") from error
        if execution_mode is SessionExecutionMode.LOCAL and worktree_id is not None:
            raise ValueError("local Session must not have a Worktree binding")
        if projectless and (
            execution_mode is not SessionExecutionMode.LOCAL
            or worktree_id is not None
            or project_id is not None
        ):
            raise ValueError("projectless Session must use local execution without a binding")
        if associated_worktree_id is not None:
            raise ValueError("new Session must not have an associated Worktree")
        if execution_mode is SessionExecutionMode.WORKTREE and worktree_id is None:
            raise ValueError("worktree Session must have a Worktree binding")
        if execution_mode is SessionExecutionMode.WORKTREE:
            associated_worktree_id = worktree_id

        def write(connection: sqlite3.Connection) -> CommittedMutation[Session]:
            session = session_from_row({
                "id": session_id, "workspace_root": str(workspace), "worktree_id": worktree_id,
                "associated_worktree_id": associated_worktree_id, "execution_mode": execution_mode.value,
                "title": title, "task_status": "new", "created_at": now, "updated_at": now,
            })
            if projectless_creation_id is not None:
                reservation = connection.execute(
                    "SELECT * FROM projectless_workspace_creations WHERE operation_id = ?",
                    (projectless_creation_id,),
                ).fetchone()
                if reservation is None or reservation["status"] != "created" or (
                    reservation["session_id"], reservation["workspace_root"],
                    reservation["workspace_dev"], reservation["workspace_inode"], reservation["workspace_uid"],
                ) != (session_id, str(workspace), metadata.st_dev, metadata.st_ino, metadata.st_uid):
                    raise WorkspaceBoundaryError("projectless workspace identity changed")
            if worktree_id is not None:
                worktree = connection.execute(
                    """
                    SELECT p.id, w.repository_root
                    FROM worktrees w
                    JOIN projects p ON p.id = w.project_id
                    WHERE w.id = ?
                    """,
                    (worktree_id,),
                ).fetchone()
                if worktree is None:
                    raise ResourceNotFoundError("worktree not found")
                source_root = worktree["repository_root"]
                if source_root is None or not workspace.is_relative_to(Path(source_root)):
                    raise WorkspaceBoundaryError(
                        "session repository does not match worktree"
                    )
                if project_id is not None and worktree["id"] != project_id:
                    raise WorkspaceBoundaryError(
                        "session project does not match worktree"
                    )
            elif project_id is not None:
                project = connection.execute(
                    "SELECT id, workspace_root FROM projects WHERE id = ?",
                    (project_id,),
                ).fetchone()
                if project is None:
                    raise ResourceNotFoundError("project not found")
                if project["workspace_root"] != str(workspace) and workspace_roots is None:
                    raise WorkspaceBoundaryError("session workspace does not match project")
            elif not projectless:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO projects (
                        id, name, workspace_root, git_repository_root,
                        git_common_dir, created_at, updated_at
                    ) VALUES (?, ?, ?, NULL, NULL, ?, ?)
                    """,
                    (
                        direct_project_id(str(workspace)),
                        default_project_name(str(workspace)),
                        str(workspace),
                        now,
                        now,
                    ),
                )
            connection.execute(
                """
                INSERT INTO sessions (
                    id, workspace_root, workspace_dev, workspace_inode,
                    workspace_uid, worktree_id, execution_mode,
                    associated_worktree_id, title, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    str(workspace),
                    metadata.st_dev,
                    metadata.st_ino,
                    metadata.st_uid,
                    worktree_id,
                    execution_mode.value,
                    associated_worktree_id,
                    title,
                    now,
                    now,
                ),
            )
            if not projectless:
                from eidos_runtime.persistence.project_workspaces import ensure_project_root, project_roots, capture_session_roots
                from eidos_runtime.persistence.mappers.worktree import project_from_row
                bound_project = project_id
                if bound_project is None and worktree_id is not None:
                    bound_project = connection.execute("SELECT project_id FROM worktrees WHERE id=?", (worktree_id,)).fetchone()[0]
                if bound_project is None:
                    bound_project = connection.execute("SELECT id FROM projects WHERE workspace_root=?", (str(workspace),)).fetchone()[0]
                project_row = connection.execute("SELECT * FROM projects WHERE id=?", (bound_project,)).fetchone()
                saved = ensure_project_root(connection, project_from_row(project_row))
                project_git_metadata_changed = False
                if workspace_roots is not None:
                    from eidos_runtime.persistence.project_workspaces import verify_session_roots
                    verify_session_roots(workspace_roots)
                    if not workspace_roots or workspace_roots[0].path != str(workspace):
                        raise WorkspaceBoundaryError("session workspace snapshot does not match its source")
                    roots = workspace_roots
                    from eidos_runtime.persistence.project_workspaces import sync_project_git_metadata
                    project_git_metadata_changed = sync_project_git_metadata(
                        connection,
                        saved,
                        roots,
                        updated_at=now,
                    )
                else:
                    roots = capture_session_roots(project_roots(connection, bound_project))
                    roots = tuple(sorted(roots, key=lambda root: root.id != saved.primary_root_id))
                connection.execute("UPDATE sessions SET project_id=?,workspace_roots_json=? WHERE id=?", (
                    bound_project, json.dumps([root.model_dump(mode="json") for root in roots]), session_id,
                ))
                session = session.model_copy(update={"project_id": bound_project, "roots": roots})
            else:
                project_git_metadata_changed = False
            events = []
            if project_git_metadata_changed:
                events.append(
                    append_event(
                        connection,
                        EventType.PROJECT_UPDATED,
                        now,
                        {"id": bound_project},
                    )
                )
            event = append_event(
                connection,
                EventType.SESSION_CREATED,
                now,
                {"session": session_to_legacy_dict(session)},
                session_id=session_id,
            )
            events.append(event)
            if projectless_creation_id is not None:
                connection.execute(
                    "UPDATE projectless_workspace_creations SET status = 'completed' WHERE operation_id = ?",
                    (projectless_creation_id,),
                )
            return CommittedMutation(session, tuple(events))

        if transaction_connection is not None:
            return write(transaction_connection)
        return self._write_committed(
            write,
            operation_id=operation_id,
            operation_scope="session/create",
            operation_request=operation_request or {
                "workspaceRoot": str(workspace),
                "projectless": projectless,
                **({"projectId": project_id} if project_id is not None else {}),
                **({"roots": [root.model_dump(mode="json") for root in workspace_roots]} if workspace_roots is not None else {}),
                **({"title": title} if title is not None else {}),
            },
            serialize_value=session_to_operation_dict,
            deserialize_value=session_from_legacy_dict,
        )

    def list_sessions(
        self, *, limit: int = DEFAULT_LIST_LIMIT, cursor: str | None = None
    ) -> SessionPage:
        page, next_cursor = self._list_session_rows(limit=limit, cursor=cursor)
        return SessionPage(
            items=tuple(session_from_row(row) for row in page),
            next_cursor=next_cursor,
        )

    def list_session_projections(
        self, *, limit: int = DEFAULT_LIST_LIMIT, cursor: str | None = None
    ) -> SessionProjectionPage:
        page, next_cursor = self._list_session_rows(limit=limit, cursor=cursor)
        return SessionProjectionPage(
            items=tuple(session_projection_from_row(row) for row in page),
            next_cursor=next_cursor,
        )

    def _list_session_rows(
        self, *, limit: int, cursor: str | None
    ) -> tuple[list[sqlite3.Row], str | None]:
        cursor_state = _decode_cursor(cursor) if cursor is not None else None
        sql = SESSION_SELECT
        with self.lock:
            connection = self._connection()
            if cursor_state is None:
                high_water = connection.execute(
                    "SELECT COALESCE(MAX(creation_seq), 0) FROM sessions"
                ).fetchone()[0]
                before_sequence = high_water + 1
            else:
                high_water, before_sequence = cursor_state
            rows = connection.execute(
                sql
                + " WHERE s.creation_seq <= ? AND s.creation_seq < ? AND NOT EXISTS (SELECT 1 FROM agent_delegations d WHERE d.child_session_id=s.id)"
                + " ORDER BY s.creation_seq DESC LIMIT ?",
                (high_water, before_sequence, limit + 1),
            ).fetchall()
        has_more = len(rows) > limit
        page = rows[:limit]
        return page, (
                _encode_cursor(
                high_water, page[-1]["creation_seq"]
                )
                if has_more
                else None
        )

    def read_session(self, session_id: str) -> Session | None:
        with self.lock:
            row = self._connection().execute(
                SESSION_SELECT + " WHERE s.id = ?",
                (session_id,),
            ).fetchone()
        return session_from_row(row) if row is not None else None

    def legacy_project_local_session_create_result(
        self,
        operation_id: str,
        project_id: str,
    ) -> dict[str, object] | None:
        """Replay completed pre-project-binding Session creates safely.

        Older builds hashed a project-bound local create as only its canonical
        workspace root and ``projectless`` flag. The old request hash cannot
        identify the Project, so the persisted Session remains the authority
        for deciding whether this retry belongs to the selected Project.
        """

        with self.lock:
            row = self._connection().execute(
                "SELECT request_hash, status, result_json FROM operations "
                "WHERE id = ? AND scope = 'session/create'",
                (operation_id,),
            ).fetchone()
        if row is None or row["status"] != "completed" or row["result_json"] is None:
            return None
        try:
            result = json.loads(row["result_json"])
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(result, dict):
            return None
        session_id = result.get("id")
        stored_root = result.get("workspaceRoot")
        if not isinstance(session_id, str) or not isinstance(stored_root, str):
            return None
        if not Path(stored_root).is_absolute():
            return None
        legacy_request = {"workspaceRoot": stored_root, "projectless": False}
        if row["request_hash"] != canonical_hash(legacy_request):
            return None

        session = self.read_session(session_id)
        if (
            session is None
            or session.project_id != project_id
            or session.workspace_root != stored_root
            or session.execution_mode is not SessionExecutionMode.LOCAL
            or session.worktree_id is not None
        ):
            return None
        return result

    def find_for_worktree(self, worktree_id: str) -> Session | None:
        """Return the oldest Session bound to or associated with a Worktree."""

        with self.lock:
            row = self._connection().execute(
                SESSION_SELECT
                + " WHERE s.worktree_id = ? OR s.associated_worktree_id = ?"
                + " ORDER BY s.created_at ASC LIMIT 1",
                (worktree_id, worktree_id),
            ).fetchone()
        return session_from_row(row) if row is not None else None

    def read_session_projection(self, session_id: str) -> SessionProjection | None:
        with self.lock:
            row = self._connection().execute(
                SESSION_SELECT + " WHERE s.id = ?",
                (session_id,),
            ).fetchone()
        return session_projection_from_row(row) if row is not None else None

    def session_is_projectless(self, session_id: str) -> bool:
        with self.lock:
            row = self._connection().execute(
                """
                SELECT 1
                FROM sessions s
                WHERE s.id = ?
                  AND s.worktree_id IS NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM projects p WHERE p.id = s.project_id OR (s.project_id IS NULL AND p.workspace_root = s.workspace_root)
                  )
                """,
                (session_id,),
            ).fetchone()
        return row is not None

    def run_is_projectless(self, run_id: str) -> bool:
        with self.lock:
            row = self._connection().execute(
                """
                SELECT 1
                FROM runs r
                JOIN sessions s ON s.id = r.session_id
                WHERE r.id = ?
                  AND s.worktree_id IS NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM projects p WHERE p.id = s.project_id OR (s.project_id IS NULL AND p.workspace_root = s.workspace_root)
                  )
                """,
                (run_id,),
            ).fetchone()
        return row is not None

    def session_model_id(self, session_id: str) -> str | None:
        with self.lock:
            row = self._connection().execute(
                """
                SELECT model_id FROM runs
                WHERE session_id = ?
                ORDER BY creation_seq LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        return str(row["model_id"]) if row is not None else None

    def rename_session(
        self,
        session_id: str,
        title: str,
        *,
        operation_id: str | None = None,
    ) -> Session:
        return self.rename_session_committed(
            session_id, title, operation_id=operation_id
        ).value

    def rename_session_committed(
        self,
        session_id: str,
        title: str,
        *,
        operation_id: str | None = None,
    ) -> CommittedMutation[Session]:
        if not title or len(title) > 60 or len(title.encode("utf-8")) > 120:
            raise ValueError("session title is invalid")
        now = _now_ms()

        def write(connection: sqlite3.Connection) -> CommittedMutation[Session]:
            updated = connection.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                (title, now, session_id),
            )
            if updated.rowcount != 1:
                raise ResourceNotFoundError("session not found")
            event = append_event(
                connection,
                EventType.SESSION_TITLE_UPDATED,
                now,
                {"title": title},
                session_id=session_id,
            )
            row = connection.execute(
                SESSION_SELECT + " WHERE s.id = ?", (session_id,)
            ).fetchone()
            return CommittedMutation(session_from_row(row), (event,))

        return self._write_committed(
            write,
            operation_id=operation_id,
            operation_scope="session/rename",
            operation_request={"sessionId": session_id, "title": title},
            serialize_value=session_to_legacy_dict,
            deserialize_value=session_from_legacy_dict,
        )

    def begin_title_generation_committed(
        self, session_id: str
    ) -> CommittedMutation[Session]:
        with self.lock, self._connection() as connection:
            row = connection.execute(
                "SELECT id FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                raise ResourceNotFoundError("session not found")
            event = append_event(
                connection,
                EventType.SESSION_TITLE_GENERATION_STARTED,
                _now_ms(),
                {},
                session_id=session_id,
            )
        session = self.read_session(session_id)
        assert session is not None
        return CommittedMutation(session, (event,))

    def finish_title_generation_committed(
        self,
        session_id: str,
        title: str,
        *,
        failure_reason: str | None = None,
    ) -> CommittedMutation[Session]:
        if not title or len(title) > 60 or len(title.encode("utf-8")) > 120:
            raise ValueError("session title is invalid")
        events: list[dict[str, object]] = []
        with self.lock, self._connection() as connection:
            row = connection.execute(
                "SELECT title FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                raise ResourceNotFoundError("session not found")
            now = _now_ms()
            if failure_reason is not None:
                events.append(append_event(
                    connection,
                    EventType.SESSION_TITLE_GENERATION_FAILED,
                    now,
                    {"reason": failure_reason},
                    session_id=session_id,
                ))
            if row["title"] is None:
                connection.execute(
                    """
                    UPDATE sessions SET title = ?, updated_at = ?
                    WHERE id = ? AND title IS NULL
                    """,
                    (title, now, session_id),
                )
                events.append(append_event(
                    connection,
                    EventType.SESSION_TITLE_UPDATED,
                    now,
                    {"title": title},
                    session_id=session_id,
                ))
        session = self.read_session(session_id)
        assert session is not None
        return CommittedMutation(session, tuple(events))

    def delete_session(
        self,
        session_id: str,
        *,
        operation_id: str | None = None,
    ) -> DeletedSession:
        return self.delete_session_committed(
            session_id, operation_id=operation_id
        ).value

    def list_empty_session_ids_for_project(self, project_id: str) -> tuple[str, ...]:
        """Find legacy no-Run Sessions that are safe to remove during Project delete."""

        with self.lock:
            rows = self._connection().execute(
                """
                SELECT s.id
                FROM sessions s
                LEFT JOIN projects direct_p ON direct_p.id = s.project_id OR (s.project_id IS NULL AND direct_p.workspace_root = s.workspace_root)
                WHERE COALESCE(TRIM(s.title), '') = ''
                  AND NOT EXISTS (
                      SELECT 1 FROM runs r WHERE r.session_id = s.id
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM session_forks f WHERE f.session_id = s.id
                  )
                  AND (
                      direct_p.id = ?
                      OR EXISTS (
                          SELECT 1
                          FROM worktrees w
                          WHERE w.project_id = ?
                            AND (w.id = s.worktree_id OR w.id = s.associated_worktree_id)
                      )
                  )
                ORDER BY s.creation_seq ASC
                """,
                (project_id, project_id),
            ).fetchall()
        return tuple(str(row["id"]) for row in rows)

    def assert_session_deletable(self, session_id: str) -> None:
        """Check the durable preconditions before an external Git removal."""

        with self.lock:
            self._assert_session_deletable(self._connection(), session_id)

    def assert_session_idle(self, session_id: str) -> None:
        with self.lock:
            self._assert_session_deletable(self._connection(), session_id)

    def update_execution_binding_committed(
        self,
        session_id: str,
        *,
        execution_mode: SessionExecutionMode,
        worktree_id: str | None,
        associated_worktree_id: str | None,
    ) -> CommittedMutation[Session]:
        if execution_mode is SessionExecutionMode.LOCAL and worktree_id is not None:
            raise ValueError("local Session must not have an active Worktree")
        if execution_mode is SessionExecutionMode.WORKTREE and (
            worktree_id is None or associated_worktree_id != worktree_id
        ):
            raise ValueError("worktree Session binding is incomplete")
        now = _now_ms()

        def write(connection: sqlite3.Connection) -> CommittedMutation[Session]:
            self._assert_session_deletable(connection, session_id)
            current = connection.execute(
                "SELECT worktree_id, associated_worktree_id FROM sessions "
                "WHERE id = ?",
                (session_id,),
            ).fetchone()
            assert current is not None
            if worktree_id is not None:
                worktree = connection.execute(
                    "SELECT p.workspace_root FROM worktrees w "
                    "JOIN projects p ON p.id = w.project_id WHERE w.id = ?",
                    (worktree_id,),
                ).fetchone()
                if worktree is None:
                    raise ResourceNotFoundError("worktree not found")
                if associated_worktree_id != worktree_id:
                    raise ValueError("active and associated Worktree differ")
            connection.execute(
                "UPDATE sessions SET execution_mode = ?, worktree_id = ?, "
                "associated_worktree_id = ?, updated_at = ? WHERE id = ?",
                (
                    execution_mode.value,
                    worktree_id,
                    associated_worktree_id,
                    now,
                    session_id,
                ),
            )
            event = append_event(
                connection,
                EventType.SESSION_HANDOFF_COMPLETED,
                now,
                {
                    "executionMode": execution_mode.value,
                    "worktreeId": worktree_id,
                    "associatedWorktreeId": associated_worktree_id,
                },
                session_id=session_id,
            )
            row = connection.execute(
                SESSION_SELECT + " WHERE s.id = ?", (session_id,)
            ).fetchone()
            return CommittedMutation(session_from_row(row), (event,))

        return self._write_committed(
            write,
            operation_id=None,
            operation_scope="session/handoff-binding",
            operation_request={
                "sessionId": session_id,
                "executionMode": execution_mode.value,
                "worktreeId": worktree_id,
                "associatedWorktreeId": associated_worktree_id,
            },
            serialize_value=session_to_operation_dict,
            deserialize_value=session_from_legacy_dict,
        )

    def delete_session_committed(
        self,
        session_id: str,
        *,
        operation_id: str | None = None,
        transaction_connection: sqlite3.Connection | None = None,
    ) -> CommittedMutation[DeletedSession]:
        def write(
            connection: sqlite3.Connection, session_id: str = session_id,
        ) -> CommittedMutation[DeletedSession]:
            self._assert_session_deletable(connection, session_id)
            from eidos_runtime.memory.lifecycle import invalidate_source
            invalidate_source(connection, session_id, reason='delete')
            children = connection.execute(
                'SELECT child_session_id FROM agent_delegations WHERE parent_run_id IN (SELECT id FROM runs WHERE session_id=?)',
                (session_id,),
            ).fetchall()
            connection.execute('DELETE FROM agent_messages WHERE parent_run_id IN (SELECT id FROM runs WHERE session_id=?)', (session_id,))
            connection.execute('DELETE FROM agent_delegations WHERE parent_run_id IN (SELECT id FROM runs WHERE session_id=?)', (session_id,))
            for child in children:
                # Delete borrowed Session facts only; never remove its workspace.
                write(connection, child['child_session_id'])
            connection.execute('DELETE FROM agent_waits WHERE run_id IN (SELECT id FROM runs WHERE session_id=?)', (session_id,))
            run_ids = "SELECT id FROM runs WHERE session_id = ?"
            connection.execute(
                f"DELETE FROM durable_intents WHERE run_id IN ({run_ids})",
                (session_id,),
            )
            connection.execute(
                f"DELETE FROM approvals WHERE run_id IN ({run_ids})", (session_id,)
            )
            connection.execute(
                """
                DELETE FROM tool_attempts WHERE tool_call_id IN (
                    SELECT tool_calls.id FROM tool_calls
                    JOIN items ON items.id = tool_calls.item_id
                    WHERE items.session_id = ?
                )
                """,
                (session_id,),
            )
            connection.execute(
                f"DELETE FROM finalization_attempts WHERE run_id IN ({run_ids})",
                (session_id,),
            )
            connection.execute(
                f"""
                DELETE FROM checkpoint_actions
                WHERE source_run_id IN ({run_ids})
                   OR target_run_id IN ({run_ids})
                   OR checkpoint_id IN (
                       SELECT id FROM checkpoints WHERE run_id IN ({run_ids})
                   )
                """,
                (session_id, session_id, session_id),
            )
            connection.execute(
                f"DELETE FROM checkpoints WHERE run_id IN ({run_ids})",
                (session_id,),
            )
            connection.execute(
                f"DELETE FROM run_dependency_bindings WHERE run_id IN ({run_ids})",
                (session_id,),
            )
            connection.execute(
                f"DELETE FROM run_dependency_snapshots WHERE run_id IN ({run_ids})",
                (session_id,),
            )
            connection.execute(
                f"DELETE FROM run_repository_retrievals WHERE run_id IN ({run_ids})",
                (session_id,),
            )
            connection.execute(
                f"UPDATE model_attempts SET context_snapshot_id = NULL WHERE step_id IN ("
                f"SELECT steps.id FROM steps WHERE steps.run_id IN ({run_ids})"
                f")",
                (session_id,),
            )
            connection.execute(
                f"DELETE FROM context_snapshots WHERE run_id IN ({run_ids})",
                (session_id,),
            )
            connection.execute(
                f"DELETE FROM context_plans WHERE run_id IN ({run_ids})",
                (session_id,),
            )
            connection.execute(
                f"DELETE FROM verified_compact_summaries WHERE run_id IN ({run_ids})",
                (session_id,),
            )
            connection.execute(
                """
                DELETE FROM model_attempts WHERE step_id IN (
                    SELECT steps.id FROM steps
                    JOIN runs ON runs.id = steps.run_id
                    WHERE runs.session_id = ?
                )
                """,
                (session_id,),
            )
            connection.execute(
                f"DELETE FROM steps WHERE run_id IN ({run_ids})", (session_id,)
            )
            connection.execute(
                """
                DELETE FROM step_resolution_snapshots
                WHERE run_snapshot_id IN (
                    SELECT id FROM run_resolution_snapshots
                    WHERE run_id IN (
                        SELECT id FROM runs WHERE session_id = ?
                    )
                )
                """,
                (session_id,),
            )
            connection.execute(
                f"DELETE FROM execution_segments WHERE run_id IN ({run_ids})",
                (session_id,),
            )
            connection.execute(
                f"DELETE FROM input_mailbox WHERE run_id IN ({run_ids})",
                (session_id,),
            )
            connection.execute(
                "DELETE FROM compact_summaries WHERE session_id = ?", (session_id,)
            )
            connection.execute(
                """
                DELETE FROM tool_calls WHERE item_id IN (
                    SELECT id FROM items WHERE session_id = ?
                )
                """,
                (session_id,),
            )
            connection.execute("DELETE FROM items WHERE session_id = ?", (session_id,))
            connection.execute(
                """
                DELETE FROM event_outbox
                WHERE event_id IN (
                    SELECT id FROM events WHERE session_id = ?
                )
                """,
                (session_id,),
            )
            connection.execute(
                "DELETE FROM events WHERE session_id = ?", (session_id,)
            )
            connection.execute(
                """
                DELETE FROM run_resolution_snapshots
                WHERE run_id IN (
                    SELECT id FROM runs WHERE session_id = ?
                )
                """,
                (session_id,),
            )
            connection.execute("DELETE FROM runs WHERE session_id = ?", (session_id,))
            connection.execute(
                """
                DELETE FROM rule_resolution_snapshots
                WHERE NOT EXISTS (
                    SELECT 1 FROM step_resolution_snapshots
                    WHERE step_resolution_snapshots.rule_snapshot_id =
                          rule_resolution_snapshots.id
                )
                """
            )
            # Worktree rows are durable lifecycle records and remain in the
            # database after managed removal. Clear both Session bindings
            # before deleting the Session so the restrictive foreign keys do
            # not leave a deleted Session pointing at a historical Worktree.
            connection.execute(
                "UPDATE sessions SET worktree_id = NULL, "
                "associated_worktree_id = NULL WHERE id = ?",
                (session_id,),
            )
            connection.execute(
                "DELETE FROM session_handoff_operations WHERE session_id = ?",
                (session_id,),
            )
            from eidos_runtime.persistence.sidebar import remove_sidebar_pin

            pin_events = remove_sidebar_pin(connection, "session", session_id)
            from eidos_runtime.persistence.automations import AutomationRepository
            AutomationRepository.pause_targets(connection, session_id=session_id)
            connection.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            return CommittedMutation(
                DeletedSession(deleted_session_id=session_id), pin_events
            )

        if transaction_connection is not None:
            return write(transaction_connection)
        return self._write_committed(
            write,
            operation_id=operation_id,
            operation_scope="session/delete",
            operation_request={"sessionId": session_id},
            serialize_value=deleted_session_to_legacy_dict,
            deserialize_value=deleted_session_from_legacy_dict,
        )

    @staticmethod
    def _assert_session_deletable(
        connection: sqlite3.Connection, session_id: str
    ) -> None:
        session = connection.execute(
            "SELECT id FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if session is None:
            raise ResourceNotFoundError("session not found")
        if connection.execute('SELECT 1 FROM agent_delegations WHERE child_session_id=?', (session_id,)).fetchone():
            raise SessionActiveError("child sessions are managed by their parent")
        if connection.execute(
            "SELECT 1 FROM agent_delegations d JOIN runs p ON p.id=d.parent_run_id JOIN runs c ON c.session_id=d.child_session_id WHERE p.session_id=? AND c.status IN ('queued','running','waiting_approval','waiting_input','waiting_agents','finalizing') LIMIT 1",
            (session_id,),
        ).fetchone():
            raise SessionActiveError("session has an active child run")
        active = connection.execute(
            """
            SELECT 1 FROM runs
            WHERE session_id = ? AND status IN (
                'queued', 'running', 'waiting_approval', 'waiting_input', 'waiting_agents', 'finalizing'
            ) LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        if active is not None:
            raise SessionActiveError("session has an active run")

    def read_tool_text(
        self, session_id: str, tool_call_id: str, field: Literal["diff", "result", "content"],
        sha256: str, offset: int,
    ) -> ToolTextPage:
        if field not in {"diff", "result", "content"} or offset < 0:
            raise InvalidCursorError("invalid tool text page")
        if field == "content":
            with self.lock:
                row = self._connection().execute(
                    "SELECT content FROM items WHERE id=? AND session_id=? AND kind='assistant_message'",
                    (tool_call_id, session_id),
                ).fetchone()
            if row is None:
                from eidos_runtime.persistence.session_history import SessionHistoryRepository
                record = SessionHistoryRepository(self.database).item_record(session_id, tool_call_id)
                if record is None or record.item.kind != "assistant_message":
                    raise ResourceNotFoundError("assistant item not found in session")
                row = {"content": record.item.content}
        else:
            column = "approval_diff" if field == "diff" else "ui_result_json"
            with self.lock:
                row = self._connection().execute(
                    f"SELECT tool_calls.{column} AS content, tool_calls.result_json "
                    "FROM tool_calls JOIN items ON items.id = tool_calls.item_id "
                    "WHERE tool_calls.id = ? AND items.session_id = ?",
                    (tool_call_id, session_id),
                ).fetchone()
            if row is None:
                from eidos_runtime.persistence.session_history import SessionHistoryRepository
                historical = SessionHistoryRepository(self.database).tool_record(session_id, tool_call_id)
                if historical is None or historical.item.tool_call is None:
                    raise ResourceNotFoundError("tool call not found in session")
                tool = historical.item.tool_call
                row = {"content": tool.change_diff if field == "diff" else tool.result_json, "result_json": tool.result_json}
        content = row["content"]
        if content is None and field == "result":
            content = row["result_json"]
        if not isinstance(content, str):
            raise ResourceNotFoundError("tool text unavailable")
        # ponytail: hash the bounded text per page; index hashes only if reads become costly.
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if digest != sha256:
            raise InvalidCursorError("tool text changed; reload the session before reading")
        if offset > len(content):
            raise InvalidCursorError("tool text offset exceeds content")
        end = min(len(content), offset + TOOL_TEXT_PAGE_CHARACTERS)
        return ToolTextPage(content=content[offset:end], next_offset=end,
                            total_characters=len(content), sha256=digest)

    def read_session_snapshot(
        self,
        session_id: str,
        *,
        item_limit: int = 200,
        before_item_id: str | None = None,
    ) -> dict[str, object]:
        with self.lock:
            connection = self._connection()
            session_row = connection.execute(
                SESSION_SELECT + " WHERE s.id = ?", (session_id,)
            ).fetchone()
            if session_row is None:
                raise ResourceNotFoundError("session not found")
            before_sequence: int | None = None
            before_timestamp: int | None = None
            from eidos_runtime.persistence.session_history import SessionHistoryRepository
            history = SessionHistoryRepository(self.database)
            inherited_cursor = before_item_id is not None and connection.execute(
                "SELECT 1 FROM session_history_entries WHERE session_id=? AND item_id=?",
                (session_id, before_item_id),
            ).fetchone() is not None
            if inherited_cursor:
                before_sequence = 0
            elif before_item_id is not None:
                before_row = connection.execute(
                    """
                    SELECT creation_seq,created_at FROM items
                    WHERE id = ? AND session_id = ?
                    """,
                    (before_item_id, session_id),
                ).fetchone()
                if before_row is None:
                    raise ResourceNotFoundError("item not found")
                before_sequence = before_row["creation_seq"]
                before_timestamp = before_row["created_at"]
            run_rows = connection.execute(
                """
                SELECT * FROM runs WHERE session_id = ?
                ORDER BY creation_seq DESC LIMIT 100
                """,
                (session_id,),
            ).fetchall()
            item_sql = "SELECT * FROM items WHERE session_id = ?"
            item_parameters: list[object] = [session_id]
            if before_sequence is not None:
                item_sql += " AND creation_seq < ?"
                item_parameters.append(before_sequence)
            item_sql += " ORDER BY creation_seq DESC LIMIT ?"
            item_parameters.append(item_limit + 1)
            item_rows = connection.execute(item_sql, item_parameters).fetchall()
            known_run_ids = {row["id"] for row in run_rows}
            missing_run_ids = sorted(
                {row["run_id"] for row in item_rows[:item_limit]} - known_run_ids
            )
            if missing_run_ids:
                placeholders = ",".join("?" for _ in missing_run_ids)
                run_rows.extend(
                    connection.execute(
                        f"SELECT * FROM runs WHERE session_id = ? AND id IN ({placeholders})",
                        [session_id, *missing_run_ids],
                    ).fetchall()
                )
            tool_rows: list[sqlite3.Row] = []
            if item_rows:
                placeholders = ",".join("?" for _ in item_rows)
                tool_rows = connection.execute(
                    f"SELECT * FROM tool_calls WHERE item_id IN ({placeholders})",
                    [row["id"] for row in item_rows],
                ).fetchall()
            through_event_id = connection.execute(
                "SELECT COALESCE(MAX(id), 0) FROM events WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
        session = session_to_legacy_dict(session_from_row(session_row))
        from eidos_runtime.persistence.automations import AutomationRepository
        automation_origins = AutomationRepository(self.database).origins_for_runs([row["id"] for row in run_rows])
        from eidos_runtime.persistence.channels import ChannelRepository
        channel_origins = ChannelRepository(self.database).origins_for_runs([row["id"] for row in run_rows])
        selected_runs = [
            _run_from_row(row, include_user_input=False, automation_origin=automation_origins.get(row["id"]), channel_origin=channel_origins.get(row["id"]))
            for row in reversed(run_rows)
            if row["id"] in known_run_ids
        ]
        remaining_runs = {
            row["id"]: _run_from_row(row, include_user_input=False, automation_origin=automation_origins.get(row["id"]), channel_origin=channel_origins.get(row["id"]))
            for row in run_rows
            if row["id"] not in known_run_ids
        }
        from eidos_runtime.persistence.hooks import HookRepository
        hook_repository = HookRepository(self.database)
        for run in (*selected_runs, *remaining_runs.values()):
            activity = hook_repository.activities(run_id=str(run["id"]), limit=8, previews=False).items
            if activity:
                run["hookActivity"] = [entry.to_wire_dict() for entry in activity]
        run_order = {row["id"]: row["creation_seq"] for row in run_rows}
        tools_by_item = {row["item_id"]: row for row in tool_rows}
        has_more = len(item_rows) > item_limit
        selected_items: list[dict[str, object]] = []
        selected_bytes = _json_bytes(session) + _json_bytes(selected_runs) + 1024
        for row in item_rows[:item_limit]:
            item = _snapshot_item(row, tools_by_item.get(row["id"]))
            run = remaining_runs.get(row["run_id"])
            item_bytes = _json_bytes(item) + (_json_bytes(run) if run else 0)
            if selected_bytes + item_bytes > MAX_SNAPSHOT_BYTES:
                has_more = True
                break
            if run:
                selected_runs.append(run)
                del remaining_runs[row["run_id"]]
            selected_items.append(item)
            selected_bytes += item_bytes
        selected_items.reverse()
        selected_runs.sort(key=lambda run: run_order[run["id"]])
        snapshot: dict[str, object] = {
            "session": session,
            "runs": selected_runs,
            "items": selected_items,
            # Keep the wire field; session display does not need execution snapshots.
            "stepResolutions": [],
            "throughEventId": through_event_id,
        }
        if has_more and selected_items:
            snapshot["previousItemId"] = selected_items[0]["id"]
        if not has_more:
            history_page = history.page(
                session_id, limit=max(0, item_limit - len(selected_items)),
                before_item_id=before_item_id if inherited_cursor else None,
                byte_budget=max(0, MAX_SNAPSHOT_BYTES - _json_bytes(snapshot)),
            )
            if history_page.records:
                snapshot["inheritedHistory"] = [history.view(record) for record in history_page.records]
            if history_page.previous_item_id:
                snapshot["previousItemId"] = history_page.previous_item_id
            elif not history_page.records and connection.execute(
                "SELECT 1 FROM session_history_entries WHERE session_id=?", (session_id,),
            ).fetchone() and selected_items:
                snapshot["previousItemId"] = selected_items[0]["id"]
        if selected_items:
            from eidos_runtime.persistence.context_management import ContextManagementRepository
            activities = ContextManagementRepository(self.database).activities(
                session_id, tuple(dict.fromkeys(str(item["runId"]) for item in selected_items)),
                through_event_id=through_event_id,
                since=min(int(item["createdAt"]) for item in selected_items),
                before=before_timestamp, byte_budget=max(0, MAX_SNAPSHOT_BYTES - _json_bytes(snapshot) - 64),
            )
            if activities:
                snapshot["contextCompactions"] = [activity.to_wire_dict() for activity in activities]
        return snapshot

    def list_events(
        self, session_id: str, *, after_event_id: int, limit: int = 200
    ) -> dict[str, object]:
        if after_event_id < 0 or not 1 <= limit <= 500:
            raise ValueError("invalid event cursor")
        with self.lock:
            rows = self._connection().execute(
                """
                SELECT * FROM events
                WHERE session_id = ? AND id > ?
                ORDER BY id ASC LIMIT ?
                """,
                (session_id, after_event_id, limit + 1),
            ).fetchall()
        events = [event for row in rows[:limit] if (event := event_from_row(row)) is not None]
        return {
            "items": events,
            "hasMore": len(rows) > limit,
            "throughEventId": rows[min(len(rows), limit) - 1]["id"] if rows else after_event_id,
        }


def _canonical_workspace(value: str) -> Path:
    if not value or len(value) > 4096:
        raise WorkspaceBoundaryError("workspace path is invalid")
    path = Path(value)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise WorkspaceBoundaryError("workspace must be an existing absolute directory")
    return path.resolve()

def _encode_cursor(high_water: int, before_sequence: int) -> str:
    payload = (
        SESSION_CURSOR_PREFIX
        + json.dumps(
            {
                "scope": "sessions",
                "order": "creation_seq_desc",
                "highWater": high_water,
                "before": before_sequence,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    ).encode("ascii")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

def _decode_cursor(cursor: str) -> tuple[int, int]:
    if not cursor or len(cursor) > 512:
        raise InvalidCursorError("cursor is invalid")
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(
            cursor + padding,
            altchars=b"-_",
            validate=True,
        ).decode("ascii")
        if not decoded.startswith(SESSION_CURSOR_PREFIX):
            raise ValueError
        state = json.loads(decoded.removeprefix(SESSION_CURSOR_PREFIX))
        if (
            not isinstance(state, dict)
            or set(state) != {"scope", "order", "highWater", "before"}
            or state["scope"] != "sessions"
            or state["order"] != "creation_seq_desc"
            or not isinstance(state["highWater"], int)
            or not isinstance(state["before"], int)
            or isinstance(state["highWater"], bool)
            or isinstance(state["before"], bool)
            or state["highWater"] < 0
            or not 0 < state["before"] <= state["highWater"]
        ):
            raise ValueError
        return state["highWater"], state["before"]
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        raise InvalidCursorError("cursor is invalid") from None
