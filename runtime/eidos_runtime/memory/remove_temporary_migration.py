"""Remove session-specific temporary memory mode while retaining revocations."""

from __future__ import annotations

import sqlite3


REMOVE_TEMPORARY_MEMORY_SQL = """
UPDATE memory_scopes SET privacy_epoch=privacy_epoch+1,generation=generation+1 WHERE id IN
 (SELECT d.scope_id FROM memory_documents d JOIN memory_evidence e ON e.document_id=d.id
  JOIN memory_revisions r ON r.document_id=e.document_id AND r.revision=e.revision
  JOIN memory_sources s ON s.session_id=e.session_id WHERE s.temporary<>0 AND r.user_owned=0);
UPDATE memory_documents SET status='quarantined' WHERE user_owned=0 AND status='active'
 AND id IN (SELECT e.document_id FROM memory_evidence e JOIN memory_sources s
  ON s.session_id=e.session_id WHERE s.temporary<>0);
UPDATE memory_source_items SET eligible=0 WHERE session_id IN
 (SELECT session_id FROM memory_sources WHERE temporary<>0);
UPDATE memory_sources SET use_epoch=use_epoch+1 WHERE temporary<>0;
ALTER TABLE memory_sources DROP COLUMN temporary;
"""


def migrate_remove_temporary_memory(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript(
            "BEGIN IMMEDIATE;\n"
            + REMOVE_TEMPORARY_MEMORY_SQL
            + "\nPRAGMA user_version=42;\nCOMMIT;"
        )
    except BaseException:
        connection.rollback()
        raise
