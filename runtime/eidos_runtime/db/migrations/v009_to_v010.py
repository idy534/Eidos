from __future__ import annotations

import sqlite3

from eidos_runtime.db.schema import V10_REPOSITORY_SCHEMA_SQL
from eidos_runtime.runtime.fault_injection import hit_fault


FROM_VERSION = 9
TO_VERSION = 10

_REQUIRED_V9_TABLES = frozenset({
    "sessions",
    "runs",
    "model_profiles",
    "model_capability_snapshots",
    "run_model_snapshots",
    "run_resolution_snapshots",
    "rule_resolution_snapshots",
    "items",
    "tool_calls",
    "approvals",
    "tool_attempts",
    "execution_segments",
    "step_resolution_snapshots",
    "steps",
    "model_attempts",
    "finalization_attempts",
    "events",
    "event_outbox",
    "operations",
    "durable_intents",
    "plugins",
    "mcp_server_states",
    "compact_summaries",
    "input_mailbox",
    "async_operations",
})

_REQUIRED_V9_COLUMNS = {
    "sessions": frozenset({"id", "workspace_root", "created_at", "updated_at"}),
    "runs": frozenset({"id", "session_id", "status", "created_at", "updated_at"}),
    "items": frozenset({"id", "session_id", "run_id", "kind", "status"}),
    "tool_calls": frozenset({"id", "item_id", "tool_name", "status"}),
    "approvals": frozenset({"id", "tool_call_id", "run_id", "status"}),
    "events": frozenset({"id", "event_type", "run_id", "payload_json"}),
    "event_outbox": frozenset({"event_id", "status"}),
}


class InvalidV9SchemaError(RuntimeError):
    pass


def verify_v9_structure(connection: sqlite3.Connection) -> None:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if not _REQUIRED_V9_TABLES <= tables:
        raise InvalidV9SchemaError("v9 schema is incomplete")
    for table, required_columns in _REQUIRED_V9_COLUMNS.items():
        columns = {
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({table})")
        }
        if not required_columns <= columns:
            raise InvalidV9SchemaError(f"v9 table is incomplete: {table}")


def verify_fts5_capability(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO repository_fts ("
        "index_snapshot_id, record_id, path, symbol, body, kind, "
        "start_line, end_line, file_hash"
        ") VALUES ('migration-probe', 'probe', 'probe.py', 'Probe', "
        "'migration probe', 'probe', 1, 1, 'hash')"
    )
    matched = connection.execute(
        "SELECT record_id FROM repository_fts "
        "WHERE repository_fts MATCH 'migration' "
        "AND index_snapshot_id = 'migration-probe'"
    ).fetchone()
    if matched is None or matched[0] != "probe":
        raise RuntimeError("fts5 verification failed")
    connection.execute(
        "DELETE FROM repository_fts WHERE index_snapshot_id = 'migration-probe'"
    )


def migrate(connection: sqlite3.Connection) -> None:
    verify_v9_structure(connection)
    executed = 0
    pending = ""
    for line in V10_REPOSITORY_SCHEMA_SQL.splitlines(keepends=True):
        pending += line
        if not sqlite3.complete_statement(pending):
            continue
        statement = pending.strip()
        pending = ""
        if not statement:
            continue
        connection.execute(statement)
        executed += 1
        if executed == 1:
            hit_fault("migration_v10_after_first_table")
    if pending.strip():
        raise RuntimeError("v10 migration SQL is incomplete")
    verify_fts5_capability(connection)
    connection.execute(f"PRAGMA user_version = {TO_VERSION}")
    hit_fault("migration_v10_after_user_version")
