"""Extend Run waiting states without changing historical rows or indexes."""
import sqlite3

COLLABORATION_SCHEMA_SQL = """
CREATE TABLE agent_delegations (
    id TEXT PRIMARY KEY,
    parent_run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    child_session_id TEXT NOT NULL UNIQUE REFERENCES sessions(id) ON DELETE RESTRICT,
    child_run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    spawn_item_id TEXT NOT NULL UNIQUE REFERENCES items(id) ON DELETE RESTRICT,
    task_name TEXT NOT NULL,
    task TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(parent_run_id, task_name)
);
CREATE INDEX agent_delegations_parent ON agent_delegations(parent_run_id);
CREATE TABLE agent_messages (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    parent_run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    sender_session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE RESTRICT,
    recipient_session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE RESTRICT,
    content TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX agent_messages_recipient ON agent_messages(recipient_session_id, sequence);
CREATE TABLE agent_waits (
    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    item_id TEXT UNIQUE REFERENCES items(id) ON DELETE CASCADE,
    agent_ids_json TEXT NOT NULL,
    deadline_at INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending', 'ready', 'canceled'))
);
"""


def migrate_collaboration(connection: sqlite3.Connection) -> None:
    table_sql = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='runs'").fetchone()[0]
    indexes = [row[0] for row in connection.execute("SELECT sql FROM sqlite_master WHERE tbl_name='runs' AND type IN ('index', 'trigger') AND sql IS NOT NULL")]
    rebuilt = table_sql.replace('CREATE TABLE runs', 'CREATE TABLE runs_collaboration', 1).replace('CREATE TABLE "runs"', 'CREATE TABLE runs_collaboration', 1)
    rebuilt = rebuilt.replace("'waiting_input',", "'waiting_input', 'waiting_agents',")
    connection.execute('PRAGMA foreign_keys = OFF')
    try:
        connection.executescript('BEGIN IMMEDIATE;\n' + rebuilt + ';\nINSERT INTO runs_collaboration SELECT * FROM runs;\nDROP TABLE runs;\nALTER TABLE runs_collaboration RENAME TO runs;\n' + ';\n'.join(indexes) + ';\n' + COLLABORATION_SCHEMA_SQL)
        if connection.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise sqlite3.IntegrityError('collaboration migration foreign key violation')
        connection.execute('PRAGMA user_version = 16')
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.execute('PRAGMA foreign_keys = ON')
