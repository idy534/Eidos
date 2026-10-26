"""Keep saved notes independent of the chat that originally informed them."""

from __future__ import annotations

import sqlite3


MEMORY_FLOW_SQL = """
ALTER TABLE memory_documents ADD COLUMN archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1));
DROP TRIGGER memory_source_change;
DROP TRIGGER memory_source_delete;
CREATE TRIGGER memory_source_change AFTER UPDATE OF content,status,incomplete ON items
 WHEN OLD.content IS NOT NEW.content OR OLD.status IS NOT NEW.status OR OLD.incomplete IS NOT NEW.incomplete BEGIN
 UPDATE memory_sources SET revision=revision+1 WHERE session_id=NEW.session_id;
 UPDATE memory_source_items SET item_revision=item_revision+1 WHERE item_id=NEW.id;
END;
CREATE TRIGGER memory_source_delete BEFORE DELETE ON items BEGIN
 UPDATE memory_sources SET revision=revision+1 WHERE session_id=OLD.session_id;
 UPDATE memory_source_items SET eligible=0,item_revision=item_revision+1 WHERE item_id=OLD.id;
END;
"""


def migrate_memory_flow(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript(
            "BEGIN IMMEDIATE;\n"
            + MEMORY_FLOW_SQL
            + "\nPRAGMA user_version=43;\nCOMMIT;"
        )
    except BaseException:
        connection.rollback()
        raise
