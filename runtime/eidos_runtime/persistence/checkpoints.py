from __future__ import annotations

import sqlite3
from typing import Literal

from pydantic import Field, StrictBool, StrictInt, StrictStr

from eidos_runtime.db.database import Database
from eidos_runtime.protocol.schemas import ClosedModel


class CheckpointRecord(ClosedModel):
    """Persisted workspace checkpoint captured before a Run executes."""

    id: StrictStr
    run_id: StrictStr = Field(alias="runId")
    item_ordinal: StrictInt = Field(alias="itemOrdinal", ge=0)
    creation_seq: StrictInt = Field(default=0, alias="creationSeq", ge=0)
    workspace_identity_hash: StrictStr = Field(alias="workspaceIdentityHash")
    git_head: StrictStr | None = Field(default=None, alias="gitHead")
    git_snapshot_id: StrictStr | None = Field(default=None, alias="gitSnapshotId")
    model_profile_snapshot_hash: StrictStr = Field(alias="modelProfileSnapshotHash")
    reconciliation_required: StrictBool = Field(
        default=False, alias="reconciliationRequired"
    )
    created_at: StrictInt = Field(alias="createdAt", ge=0)


class CheckpointActionRecord(ClosedModel):
    """Audit record for a rewind or fork triggered from a checkpoint."""

    id: StrictStr
    checkpoint_id: StrictStr = Field(alias="checkpointId")
    action: Literal["rewind", "fork"]
    source_run_id: StrictStr = Field(alias="sourceRunId")
    target_run_id: StrictStr = Field(alias="targetRunId")
    created_at: StrictInt = Field(alias="createdAt", ge=0)


class CheckpointRepository:
    """SQLite persistence repository for checkpoints and checkpoint actions."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def create(self, record: CheckpointRecord) -> CheckpointRecord:
        with self._database.lock:
            conn = self._database.connection()
            with conn:
                conn.execute(
                    """
                    INSERT INTO checkpoints (
                        id, run_id, item_ordinal, workspace_identity_hash,
                        git_head, git_snapshot_id, model_profile_snapshot_hash,
                        reconciliation_required, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.id,
                        record.run_id,
                        record.item_ordinal,
                        record.workspace_identity_hash,
                        record.git_head,
                        record.git_snapshot_id,
                        record.model_profile_snapshot_hash,
                        1 if record.reconciliation_required else 0,
                        record.created_at,
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM checkpoints WHERE id = ?",
                    (record.id,),
                ).fetchone()
        assert row is not None
        return _checkpoint_from_row(row)

    def read(self, checkpoint_id: str) -> CheckpointRecord | None:
        with self._database.lock:
            row = self._database.connection().execute(
                "SELECT * FROM checkpoints WHERE id = ?",
                (checkpoint_id,),
            ).fetchone()
        return None if row is None else _checkpoint_from_row(row)

    def list_for_session(self, session_id: str) -> list[CheckpointRecord]:
        with self._database.lock:
            rows = self._database.connection().execute(
                """
                SELECT c.*
                FROM checkpoints c
                JOIN runs r ON r.id = c.run_id
                WHERE r.session_id = ?
                ORDER BY c.created_at DESC, c.creation_seq DESC
                """,
                (session_id,),
            ).fetchall()
        return [_checkpoint_from_row(row) for row in rows]

    def record_action(self, action: CheckpointActionRecord) -> CheckpointActionRecord:
        with self._database.lock:
            conn = self._database.connection()
            with conn:
                conn.execute(
                    """
                    INSERT INTO checkpoint_actions (
                        id, checkpoint_id, action, source_run_id, target_run_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        action.id,
                        action.checkpoint_id,
                        action.action,
                        action.source_run_id,
                        action.target_run_id,
                        action.created_at,
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM checkpoint_actions WHERE id = ?",
                    (action.id,),
                ).fetchone()
        assert row is not None
        return _action_from_row(row)

    def delete_for_session(self, session_id: str) -> tuple[str, ...]:
        """Delete a Session's checkpoints and return the snapshot ids they pinned."""

        with self._database.lock:
            conn = self._database.connection()
            with conn:
                rows = conn.execute(
                    """
                    SELECT c.git_snapshot_id
                    FROM checkpoints c
                    JOIN runs r ON r.id = c.run_id
                    WHERE r.session_id = ? AND c.git_snapshot_id IS NOT NULL
                    """,
                    (session_id,),
                ).fetchall()
                conn.execute(
                    """
                    DELETE FROM checkpoint_actions
                    WHERE checkpoint_id IN (
                        SELECT c.id FROM checkpoints c
                        JOIN runs r ON r.id = c.run_id
                        WHERE r.session_id = ?
                    )
                    """,
                    (session_id,),
                )
                conn.execute(
                    """
                    DELETE FROM checkpoints
                    WHERE run_id IN (SELECT id FROM runs WHERE session_id = ?)
                    """,
                    (session_id,),
                )
        return tuple(dict.fromkeys(str(row["git_snapshot_id"]) for row in rows))

    def list_actions(self, checkpoint_id: str) -> list[CheckpointActionRecord]:
        with self._database.lock:
            rows = self._database.connection().execute(
                """
                SELECT * FROM checkpoint_actions
                WHERE checkpoint_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (checkpoint_id,),
            ).fetchall()
        return [_action_from_row(row) for row in rows]


def _checkpoint_from_row(row: sqlite3.Row) -> CheckpointRecord:
    return CheckpointRecord(
        id=str(row["id"]),
        runId=str(row["run_id"]),
        itemOrdinal=int(row["item_ordinal"]),
        creationSeq=int(row["creation_seq"]),
        workspaceIdentityHash=str(row["workspace_identity_hash"]),
        gitHead=str(row["git_head"]) if row["git_head"] is not None else None,
        gitSnapshotId=(
            str(row["git_snapshot_id"]) if row["git_snapshot_id"] is not None else None
        ),
        modelProfileSnapshotHash=str(row["model_profile_snapshot_hash"]),
        reconciliationRequired=bool(row["reconciliation_required"]),
        createdAt=int(row["created_at"]),
    )


def _action_from_row(row: sqlite3.Row) -> CheckpointActionRecord:
    raw_action = str(row["action"])
    action: Literal["rewind", "fork"] = "fork" if raw_action == "fork" else "rewind"
    return CheckpointActionRecord(
        id=str(row["id"]),
        checkpointId=str(row["checkpoint_id"]),
        action=action,
        sourceRunId=str(row["source_run_id"]),
        targetRunId=str(row["target_run_id"]),
        createdAt=int(row["created_at"]),
    )
