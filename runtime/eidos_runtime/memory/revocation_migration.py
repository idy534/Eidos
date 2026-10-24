"""Preserve document memory while repairing historical source revocation."""

from __future__ import annotations

import sqlite3


MEMORY_SOURCE_REVOCATION_SQL = """
DROP TRIGGER memory_source_change;
DROP TRIGGER memory_source_delete;
CREATE TRIGGER memory_source_change AFTER UPDATE OF content,status,incomplete ON items
 WHEN OLD.content IS NOT NEW.content OR OLD.status IS NOT NEW.status OR OLD.incomplete IS NOT NEW.incomplete BEGIN
 UPDATE memory_sources SET revision=revision+1 WHERE session_id=NEW.session_id;
 UPDATE memory_source_items SET item_revision=item_revision+1 WHERE item_id=NEW.id;
 UPDATE memory_documents SET status='quarantined' WHERE user_owned=0 AND status='active'
  AND id IN (SELECT document_id FROM memory_evidence WHERE item_id=NEW.id);
 UPDATE memory_scopes SET privacy_epoch=privacy_epoch+1,generation=generation+1 WHERE id IN
  (SELECT d.scope_id FROM memory_documents d JOIN memory_evidence e ON e.document_id=d.id
   JOIN memory_revisions r ON r.document_id=e.document_id AND r.revision=e.revision
   WHERE e.item_id=NEW.id AND r.user_owned=0);
END;
CREATE TRIGGER memory_source_delete BEFORE DELETE ON items BEGIN
 UPDATE memory_sources SET revision=revision+1 WHERE session_id=OLD.session_id;
 UPDATE memory_source_items SET eligible=0,item_revision=item_revision+1 WHERE item_id=OLD.id;
 UPDATE memory_documents SET status='quarantined' WHERE user_owned=0 AND status='active'
  AND id IN (SELECT document_id FROM memory_evidence WHERE item_id=OLD.id);
 UPDATE memory_scopes SET privacy_epoch=privacy_epoch+1,generation=generation+1 WHERE id IN
  (SELECT d.scope_id FROM memory_documents d JOIN memory_evidence e ON e.document_id=d.id
   JOIN memory_revisions r ON r.document_id=e.document_id AND r.revision=e.revision
   WHERE e.item_id=OLD.id AND r.user_owned=0);
END;
-- Revoke payloads that v40 may already have retained after a user-owned edit.
UPDATE memory_scopes SET privacy_epoch=privacy_epoch+1,generation=generation+1 WHERE id IN
 (SELECT d.scope_id FROM memory_documents d JOIN memory_evidence e ON e.document_id=d.id
  JOIN memory_revisions r ON r.document_id=e.document_id AND r.revision=e.revision
  LEFT JOIN memory_source_items i ON i.item_id=e.item_id
  LEFT JOIN memory_sources s ON s.session_id=e.session_id
  WHERE r.user_owned=0 AND (i.item_id IS NULL OR s.session_id IS NULL OR i.eligible=0
   OR i.item_revision<>e.item_revision OR s.deleted=1 OR s.temporary=1));
"""


def migrate_memory_source_revocation(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript(
            "BEGIN IMMEDIATE;\n"
            + MEMORY_SOURCE_REVOCATION_SQL
            + "\nPRAGMA user_version=41;\nCOMMIT;"
        )
    except BaseException:
        connection.rollback()
        raise
