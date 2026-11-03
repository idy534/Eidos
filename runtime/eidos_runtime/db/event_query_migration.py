from __future__ import annotations

import sqlite3


# Session snapshot, event cursor, context compaction, and context details
# queries filter by session_id, run_id, event_type, and context_snapshot_id.
EVENT_QUERY_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS events_session_id ON events(session_id, id);
CREATE INDEX IF NOT EXISTS events_session_type_id ON events(session_id, event_type, id);
CREATE INDEX IF NOT EXISTS events_run_type_id ON events(run_id, event_type, id);
CREATE INDEX IF NOT EXISTS context_plans_run_id ON context_plans(run_id);
CREATE INDEX IF NOT EXISTS context_snapshots_run_id ON context_snapshots(run_id, created_at, id);
CREATE INDEX IF NOT EXISTS model_attempts_context_snapshot ON model_attempts(context_snapshot_id);
"""


def migrate_event_query_indexes(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript(
            "BEGIN IMMEDIATE;\n"
            + EVENT_QUERY_INDEX_SQL
            + "\nPRAGMA user_version=49;\nCOMMIT;"
        )
    except sqlite3.Error:
        if connection.in_transaction:
            connection.rollback()
        raise
