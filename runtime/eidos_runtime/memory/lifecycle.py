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
    """Keep source audit current without withdrawing already saved notes."""
    connection.execute(
        "INSERT OR IGNORE INTO memory_sources(session_id) VALUES(?)", (session_id,)
    )
    connection.execute(
        "UPDATE memory_sources SET revision=revision+1,deleted=max(deleted,?) WHERE session_id=?",
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
