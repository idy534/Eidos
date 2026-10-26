from __future__ import annotations

import sqlite3


def invalidate_source(
    connection: sqlite3.Connection,
    session_id: str,
    *,
    reason: str,
    run_id: str | None = None,
    after_ordinal: int | None = None,
) -> None:
    """Revoke affected documents inside the source's own lifecycle transaction."""
    connection.execute(
        "INSERT OR IGNORE INTO memory_sources(session_id) VALUES(?)", (session_id,)
    )
    connection.execute(
        "UPDATE memory_sources SET revision=revision+1,use_epoch=use_epoch+1,deleted=max(deleted,?) WHERE session_id=?",
        (int(reason == "delete"), session_id),
    )
    condition, parameters = "session_id=?", [session_id]
    if run_id is not None:
        condition += " AND run_id=?"
        parameters.append(run_id)
    if after_ordinal is not None:
        condition += (
            " AND item_id IN (SELECT id FROM items WHERE run_id=? AND ordinal>?)"
        )
        parameters.extend([run_id, after_ordinal])
    documents = f"SELECT document_id FROM memory_evidence WHERE item_id IN (SELECT item_id FROM memory_source_items WHERE {condition})"
    connection.execute(
        "UPDATE memory_documents SET status='quarantined' WHERE user_owned=0 AND status='active' AND id IN ("
        + documents
        + ")",
        parameters,
    )
    connection.execute(
        "UPDATE memory_scopes SET privacy_epoch=privacy_epoch+1,generation=generation+1 WHERE id IN ("
        "SELECT DISTINCT d.scope_id FROM memory_documents d JOIN memory_revisions r ON r.document_id=d.id "
        "JOIN memory_evidence e ON e.document_id=r.document_id AND e.revision=r.revision "
        "WHERE r.user_owned=0 AND e.item_id IN (SELECT item_id FROM memory_source_items WHERE "
        + condition
        + "))",
        parameters,
    )
    connection.execute(
        f"UPDATE memory_source_items SET eligible=0 WHERE {condition}", parameters
    )


def fork_source(
    connection: sqlite3.Connection, source_session_id: str, target_session_id: str
) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO memory_sources(session_id) VALUES(?)",
        (target_session_id,),
    )
    connection.execute(
        "UPDATE memory_sources SET lineage_session_id=? WHERE session_id=?",
        (source_session_id, target_session_id),
    )
