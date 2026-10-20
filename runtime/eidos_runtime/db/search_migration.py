"""Rebuildable message index; triggers record work in the source transaction."""

from __future__ import annotations

import sqlite3

SEARCH_SCHEMA_SQL = """
CREATE TABLE search_index_state(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL);
INSERT INTO search_index_state VALUES(1,0);
CREATE TABLE search_documents (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    item_id TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK(source_kind IN ('item','history')),
    position INTEGER NOT NULL,
    body TEXT NOT NULL,
    folded TEXT NOT NULL,
    UNIQUE(session_id,item_id)
);
CREATE INDEX search_documents_session ON search_documents(session_id,source_kind,position);
CREATE VIRTUAL TABLE search_fts USING fts5(folded,content='search_documents',content_rowid='id',tokenize='trigram');
CREATE TRIGGER search_documents_insert AFTER INSERT ON search_documents BEGIN
    INSERT INTO search_fts(rowid,folded) VALUES(NEW.id,NEW.folded);
    UPDATE search_index_state SET revision=revision+1 WHERE id=1;
END;
CREATE TRIGGER search_documents_delete AFTER DELETE ON search_documents BEGIN
    INSERT INTO search_fts(search_fts,rowid,folded) VALUES('delete',OLD.id,OLD.folded);
    UPDATE search_index_state SET revision=revision+1 WHERE id=1;
END;
CREATE TRIGGER search_title_update AFTER UPDATE OF title ON sessions BEGIN
    UPDATE search_index_state SET revision=revision+1 WHERE id=1;
END;
CREATE TRIGGER search_session_insert AFTER INSERT ON sessions BEGIN
    UPDATE search_index_state SET revision=revision+1 WHERE id=1;
END;
CREATE TRIGGER search_session_delete AFTER DELETE ON sessions BEGIN
    UPDATE search_index_state SET revision=revision+1 WHERE id=1;
END;
CREATE TABLE search_pending (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    item_id TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK(source_kind IN ('item','history')),
    PRIMARY KEY(session_id,item_id)
);
INSERT INTO search_pending SELECT session_id,id,'item' FROM items WHERE kind IN ('user_message','assistant_message');
INSERT INTO search_pending SELECT session_id,item_id,'history' FROM session_history_entries;
CREATE TRIGGER search_item_insert AFTER INSERT ON items
WHEN NEW.kind IN ('user_message','assistant_message') BEGIN
    INSERT OR REPLACE INTO search_pending VALUES(NEW.session_id,NEW.id,'item');
END;
CREATE TRIGGER search_item_update AFTER UPDATE OF content ON items
WHEN NEW.kind IN ('user_message','assistant_message') BEGIN
    DELETE FROM search_documents WHERE session_id=NEW.session_id AND item_id=NEW.id;
    INSERT OR REPLACE INTO search_pending VALUES(NEW.session_id,NEW.id,'item');
END;
CREATE TRIGGER search_item_delete AFTER DELETE ON items BEGIN
    DELETE FROM search_documents WHERE session_id=OLD.session_id AND item_id=OLD.id;
    DELETE FROM search_pending WHERE session_id=OLD.session_id AND item_id=OLD.id;
END;
CREATE TRIGGER search_history_insert AFTER INSERT ON session_history_entries BEGIN
    INSERT OR REPLACE INTO search_pending VALUES(NEW.session_id,NEW.item_id,'history');
END;
CREATE TRIGGER search_history_delete AFTER DELETE ON session_history_entries BEGIN
    DELETE FROM search_documents WHERE session_id=OLD.session_id AND item_id=OLD.item_id;
    DELETE FROM search_pending WHERE session_id=OLD.session_id AND item_id=OLD.item_id;
END;
CREATE TRIGGER search_history_update AFTER UPDATE OF record_json ON session_history_entries BEGIN
    DELETE FROM search_documents WHERE session_id=NEW.session_id AND item_id=NEW.item_id;
    INSERT OR REPLACE INTO search_pending VALUES(NEW.session_id,NEW.item_id,'history');
END;
"""


def migrate_search(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript(
            "BEGIN IMMEDIATE;\n"
            + SEARCH_SCHEMA_SQL
            + "PRAGMA user_version=33;\nCOMMIT;"
        )
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
