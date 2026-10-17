from __future__ import annotations

import sqlite3

AUTOMATION_SCHEMA_SQL = """
CREATE TABLE scheduled_tasks (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    revision INTEGER NOT NULL CHECK (revision > 0),
    status TEXT NOT NULL CHECK (status IN ('ACTIVE','PAUSED')),
    config_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE scheduled_occurrences (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    task_id TEXT NOT NULL REFERENCES scheduled_tasks(id),
    scheduled_for TEXT NOT NULL,
    config_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('preparing','submitted','skipped','failed','interrupted')),
    run_id TEXT,
    session_id TEXT,
    reason TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE(task_id, scheduled_for)
);
CREATE INDEX scheduled_occurrences_task ON scheduled_occurrences(task_id,sequence);
CREATE UNIQUE INDEX scheduled_occurrences_preparing ON scheduled_occurrences(task_id) WHERE status='preparing';
CREATE TABLE automation_run_origins (
    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    origin_json TEXT NOT NULL
);
CREATE INDEX automation_run_origins_task ON automation_run_origins(json_extract(origin_json,'$.automationId'));
CREATE TRIGGER automation_memory_insert AFTER INSERT ON memory_source_items
WHEN EXISTS(SELECT 1 FROM items i JOIN automation_run_origins a ON a.run_id=i.run_id WHERE i.id=NEW.item_id)
BEGIN
    UPDATE memory_source_items SET eligible=0 WHERE item_id=NEW.item_id;
END;
CREATE TRIGGER automation_origin_memory AFTER INSERT ON automation_run_origins
BEGIN
    UPDATE memory_source_items SET eligible=0 WHERE item_id IN (SELECT id FROM items WHERE run_id=NEW.run_id);
END;
"""


def migrate_automations(connection: sqlite3.Connection) -> None:
    # executescript owns the explicit transaction, including version and DDL.
    try:
        connection.executescript("BEGIN IMMEDIATE;\n" + AUTOMATION_SCHEMA_SQL + "\nPRAGMA user_version=28;\nCOMMIT;")
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
