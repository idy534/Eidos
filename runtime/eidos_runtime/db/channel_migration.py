from __future__ import annotations

import sqlite3

CHANNEL_SCHEMA_SQL = """
CREATE TABLE channel_connections (
    id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    connection_json TEXT NOT NULL
);
CREATE TABLE channel_sessions (
    route TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE TABLE channel_inbound (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    snapshot_json TEXT NOT NULL,
    message_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','submitted','rejected')),
    session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
    run_id TEXT REFERENCES runs(id) ON DELETE SET NULL
);
CREATE INDEX channel_inbound_status ON channel_inbound(status,sequence);
CREATE TABLE channel_run_origins (
    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    origin_json TEXT NOT NULL
);
CREATE TABLE channel_deliveries (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    inbound_id TEXT NOT NULL REFERENCES channel_inbound(id),
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','sending','sent','failed','unknown')),
    reason TEXT,
    next_attempt INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    UNIQUE(inbound_id,kind)
);
CREATE INDEX channel_deliveries_pending ON channel_deliveries(status,next_attempt,sequence);
CREATE TRIGGER channel_memory_insert AFTER INSERT ON memory_source_items
WHEN EXISTS(SELECT 1 FROM items i JOIN channel_run_origins c ON c.run_id=i.run_id WHERE i.id=NEW.item_id)
BEGIN UPDATE memory_source_items SET eligible=0 WHERE item_id=NEW.item_id; END;
CREATE TRIGGER channel_origin_memory AFTER INSERT ON channel_run_origins
BEGIN UPDATE memory_source_items SET eligible=0 WHERE item_id IN (SELECT id FROM items WHERE run_id=NEW.run_id); END;
"""


def migrate_channels(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript("BEGIN IMMEDIATE;\n" + CHANNEL_SCHEMA_SQL + "\nPRAGMA user_version=30;\nCOMMIT;")
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
