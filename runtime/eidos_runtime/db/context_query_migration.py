from __future__ import annotations

import sqlite3


# Replay and response-phase lookups identify a Step by its Run and ordinal.
# The existing segment/ordinal index cannot serve that access path.
CONTEXT_QUERY_INDEX_SQL = """
CREATE INDEX steps_run_ordinal ON steps(run_id, ordinal);
"""


def migrate_context_query_index(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript(
            "BEGIN IMMEDIATE;\n"
            + CONTEXT_QUERY_INDEX_SQL
            + "\nPRAGMA user_version=32;\nCOMMIT;"
        )
    except sqlite3.Error:
        if connection.in_transaction:
            connection.rollback()
        raise
