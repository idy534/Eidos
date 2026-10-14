import sqlite3

SESSION_FORK_SCHEMA_SQL = """
ALTER TABLE worktree_lifecycle_operations ADD COLUMN request_hash TEXT;
CREATE TABLE session_forks (
    session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    source_json TEXT NOT NULL
);
CREATE TABLE session_history_entries (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    item_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    record_json TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    context_eligible INTEGER NOT NULL CHECK(context_eligible IN (0,1)),
    model_step_index INTEGER,
    PRIMARY KEY(session_id, sequence),
    UNIQUE(session_id, item_id)
);
CREATE TABLE session_history_resources (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    resource_id TEXT NOT NULL,
    blob_ref TEXT NOT NULL,
    input_reference_id TEXT,
    PRIMARY KEY(session_id, resource_id)
);
"""


def migrate_session_forks(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript(
            "BEGIN IMMEDIATE;\n"
            + SESSION_FORK_SCHEMA_SQL
            + "PRAGMA user_version = 26;\nCOMMIT;"
        )
    except BaseException:
        connection.rollback()
        raise


def migrate_session_fork_titles(connection: sqlite3.Connection) -> None:
    from eidos_runtime.db.database import now_ms
    from eidos_runtime.db.events import append_event
    from eidos_runtime.runtime.state_machine import EventType

    try:
        connection.execute("BEGIN IMMEDIATE")
        targets = connection.execute(
            "SELECT s.id, json_extract(f.source_json, '$.sessionId') "
            "FROM session_forks f JOIN sessions s ON s.id=f.session_id "
            "WHERE s.title IS NULL ORDER BY s.creation_seq"
        )
        for session_id, source_id in targets:
            source = connection.execute(
                "SELECT title FROM sessions WHERE id=?", (source_id,)
            ).fetchone()
            title = source[0] if source and source[0] else "新会话"
            now = now_ms()
            connection.execute(
                "UPDATE sessions SET title=?, updated_at=? WHERE id=? AND title IS NULL",
                (title, now, session_id),
            )
            append_event(
                connection,
                EventType.SESSION_TITLE_UPDATED,
                now,
                {"title": title},
                session_id=session_id,
            )
        connection.execute("PRAGMA user_version=27")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
