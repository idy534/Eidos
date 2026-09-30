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
    """Called *inside* the originating lifecycle transaction, before deletion.

    Mixed evidence is quarantined synchronously. It is never guessed to be valid
    just because some other source survives. Relearning uses surviving originals.
    """
    connection.execute(
        "INSERT OR IGNORE INTO memory_sources(session_id) VALUES(?)", (session_id,)
    )
    connection.execute(
        "UPDATE memory_sources SET revision=revision+1,deleted=max(deleted,?) WHERE session_id=?",
        (int(reason == "delete"), session_id),
    )
    condition = "session_id=?"
    parameters: list[object] = [session_id]
    if run_id is not None:
        condition += " AND run_id=?"
        parameters.append(run_id)
    if after_ordinal is not None:
        condition += (
            " AND item_id IN (SELECT id FROM items WHERE run_id=? AND ordinal>?)"
        )
        parameters.extend([run_id, after_ordinal])
    affected = f"SELECT item_id FROM memory_source_items WHERE {condition}"
    entry_ids = f"SELECT entry_id FROM memory_evidence WHERE item_id IN ({affected})"
    connection.execute(
        "UPDATE memory_entries SET status='quarantined' WHERE user_owned=0 AND status<>'forgotten' AND id IN ("
        + entry_ids
        + ")",
        parameters,
    )
    connection.execute(
        "UPDATE memory_scopes SET privacy_epoch=privacy_epoch+1,generation=generation+1 WHERE id IN (SELECT scope_id FROM memory_entries WHERE id IN ("
        + entry_ids
        + "))",
        parameters,
    )
    connection.execute(
        f"UPDATE memory_source_items SET eligible=0 WHERE {condition}", parameters
    )
    connection.execute(
        "UPDATE memory_jobs SET state='superseded',lease_token=NULL,extraction_json=NULL,proposals_json=NULL WHERE session_id=?",
        (session_id,),
    )


def fork_source(
    connection: sqlite3.Connection, source_session_id: str, target_session_id: str
) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO memory_sources(session_id) VALUES(?)",
        (target_session_id,),
    )
    connection.execute(
        "UPDATE memory_sources SET lineage_session_id=?,temporary=COALESCE((SELECT temporary FROM memory_sources WHERE session_id=?),0) WHERE session_id=?",
        (source_session_id, source_session_id, target_session_id),
    )
