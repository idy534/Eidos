"""Discard obsolete Alpha Session facts without reading or moving workspace files."""

from eidos_runtime.db.database import Database, now_ms
from eidos_runtime.db.repositories.sessions import SessionRepository


def discard_legacy_projectless_sessions(database: Database) -> int:
    """Run under the exclusive state lock before any Run worker is admitted."""
    if database.data_directory is None:
        return 0
    legacy = str(database.data_directory / f".{database.data_directory.name}-projectless") + "/"
    repository = SessionRepository(database)
    discarded = 0
    while True:
        rows = database.connection().execute(
            """SELECT s.id FROM sessions s
               WHERE substr(s.workspace_root, 1, ?) = ?
                 AND s.worktree_id IS NULL AND s.associated_worktree_id IS NULL
                 AND NOT EXISTS (SELECT 1 FROM projects p WHERE p.workspace_root = s.workspace_root)
                 AND NOT EXISTS (SELECT 1 FROM agent_delegations d WHERE d.child_session_id = s.id)
               ORDER BY s.creation_seq LIMIT 100""",
            (len(legacy), legacy),
        ).fetchall()
        if not rows:
            return discarded
        for row in rows:
            session_id = str(row["id"])
            # Recovery can requeue an old Run. No process exists under this startup lock.
            # Retiring these records must not restart it or claim its side effects resolved.
            with database.transaction() as connection:
                connection.execute(
                    """UPDATE runs SET status = 'interrupted', completed_at = ?, updated_at = ?
                       WHERE session_id = ? OR session_id IN (
                           SELECT child_session_id FROM agent_delegations
                           WHERE parent_run_id IN (SELECT id FROM runs WHERE session_id = ?)
                       )""",
                    (now_ms(), now_ms(), session_id, session_id),
                )
                repository.delete_session_committed(
                    session_id, transaction_connection=connection,
                )
            discarded += 1

