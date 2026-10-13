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
