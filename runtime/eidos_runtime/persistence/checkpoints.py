from __future__ import annotations

import hashlib
import json
import time
import uuid

from eidos_runtime.db.database import Database, Repository
from eidos_runtime.domain.checkpoint import Checkpoint
from eidos_runtime.domain.long_task import LongTaskProgress
from eidos_runtime.persistence.errors import PersistenceCorruptionError


class CheckpointRepository(Repository):
    def __init__(self, database: Database) -> None:
        super().__init__(database)

    def create(self, run_id: str) -> Checkpoint:
        now = int(time.time() * 1000)
        with self.lock, self._connection() as connection:
            run = connection.execute(
                """
                SELECT r.reconciliation_required, r.model_profile_json,
                       s.workspace_root, s.workspace_dev, s.workspace_inode,
                       s.workspace_uid
                FROM runs r JOIN sessions s ON s.id = r.session_id
                WHERE r.id = ?
                """,
                (run_id,),
            ).fetchone()
            if run is None:
                raise KeyError(run_id)
            task = connection.execute(
                "SELECT result_json FROM operations WHERE id = ? AND scope = 'long_task/control'",
                (run_id,),
            ).fetchone()
            task_progress = (
                LongTaskProgress.model_validate_json(task["result_json"])
                if task is not None else None
            )
            context = connection.execute(
                "SELECT id FROM context_snapshots WHERE run_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            compact = connection.execute(
                "SELECT id FROM verified_compact_summaries WHERE run_id = ? ORDER BY verified_at DESC, id DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            ordinal = connection.execute(
                "SELECT COALESCE(MAX(ordinal), 0) FROM items WHERE run_id = ?",
                (run_id,),
            ).fetchone()[0]
            workspace_hash = _hash({
                "root": run["workspace_root"],
                "device": run["workspace_dev"],
                "inode": run["workspace_inode"],
                "owner": run["workspace_uid"],
            })
            checkpoint = Checkpoint(
                id=str(uuid.uuid4()),
                run_id=run_id,
                item_ordinal=int(ordinal),
                rule_snapshot_id=(
                    task_progress.rule_snapshot_id if task_progress is not None else None
                ),
                repository_snapshot_id=(
                    task_progress.index_snapshot_id or task_progress.inventory_snapshot_id
                    if task_progress is not None else None
                ),
                context_snapshot_id=context["id"] if context is not None else None,
                compact_summary_id=compact["id"] if compact is not None else None,
                workspace_identity_hash=workspace_hash,
                git_head=task_progress.git_head if task_progress is not None else None,
                permission_snapshot_hash=(
                    task_progress.permission_snapshot_hash
                    if task_progress is not None else None
                ),
                model_profile_snapshot_hash=_hash_json(str(run["model_profile_json"])),
                reconciliation_required=bool(run["reconciliation_required"]),
                created_at=now,
            )
            connection.execute(
                """
                INSERT INTO checkpoints (
                    id, run_id, item_ordinal, rule_snapshot_id,
                    repository_snapshot_id, context_snapshot_id,
                    compact_summary_id, workspace_identity_hash, git_head,
                    permission_snapshot_hash, model_profile_snapshot_hash,
                    reconciliation_required, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint.id, checkpoint.run_id, checkpoint.item_ordinal,
                    checkpoint.rule_snapshot_id, checkpoint.repository_snapshot_id,
                    checkpoint.context_snapshot_id, checkpoint.compact_summary_id,
                    checkpoint.workspace_identity_hash, checkpoint.git_head,
                    checkpoint.permission_snapshot_hash,
                    checkpoint.model_profile_snapshot_hash,
                    int(checkpoint.reconciliation_required), checkpoint.created_at,
                ),
            )
        return checkpoint

    def read(self, checkpoint_id: str) -> Checkpoint | None:
        with self.lock:
            row = self._connection().execute(
                "SELECT * FROM checkpoints WHERE id = ?", (checkpoint_id,)
            ).fetchone()
        return _map_checkpoint(row)

    def list_for_run(self, run_id: str) -> tuple[Checkpoint, ...]:
        with self.lock:
            rows = self._connection().execute(
                "SELECT * FROM checkpoints WHERE run_id = ? ORDER BY item_ordinal, creation_seq",
                (run_id,),
            ).fetchall()
        return tuple(value for row in rows if (value := _map_checkpoint(row)) is not None)

    def record_action(
        self, *, checkpoint_id: str, action: str, target_run_id: str
    ) -> None:
        checkpoint = self.read(checkpoint_id)
        if checkpoint is None:
            raise KeyError(checkpoint_id)
        if action not in {"rewind", "fork"}:
            raise ValueError("invalid checkpoint action")
        with self.lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO checkpoint_actions (
                    id, checkpoint_id, action, source_run_id, target_run_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    f"checkpoint-action-{uuid.uuid4()}", checkpoint_id, action,
                    checkpoint.run_id, target_run_id, int(time.time() * 1000),
                ),
            )

    def workspace_is_compatible(self, checkpoint: Checkpoint) -> bool:
        with self.lock:
            row = self._connection().execute(
                """
                SELECT s.workspace_root, s.workspace_dev, s.workspace_inode, s.workspace_uid
                FROM runs r JOIN sessions s ON s.id = r.session_id WHERE r.id = ?
                """,
                (checkpoint.run_id,),
            ).fetchone()
        if row is None:
            return False
        return checkpoint.workspace_identity_hash == _hash({
            "root": row["workspace_root"], "device": row["workspace_dev"],
            "inode": row["workspace_inode"], "owner": row["workspace_uid"],
        })


def _map_checkpoint(row: object) -> Checkpoint | None:
    if row is None:
        return None
    try:
        return Checkpoint.model_validate({
            "id": row["id"],
            "runId": row["run_id"],
            "itemOrdinal": row["item_ordinal"],
            "ruleSnapshotId": row["rule_snapshot_id"],
            "repositorySnapshotId": row["repository_snapshot_id"],
            "contextSnapshotId": row["context_snapshot_id"],
            "compactSummaryId": row["compact_summary_id"],
            "workspaceIdentityHash": row["workspace_identity_hash"],
            "gitHead": row["git_head"],
            "permissionSnapshotHash": row["permission_snapshot_hash"],
            "modelProfileSnapshotHash": row["model_profile_snapshot_hash"],
            "reconciliationRequired": bool(row["reconciliation_required"]),
            "createdAt": row["created_at"],
        })
    except (KeyError, TypeError, ValueError):
        raise PersistenceCorruptionError(
            "persistence_record_invalid", record="checkpoint"
        ) from None


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _hash_json(value: str) -> str:
    try:
        parsed = json.loads(value)
    except ValueError:
        raise PersistenceCorruptionError(
            "persistence_record_invalid", record="run_model_snapshot"
        ) from None
    return _hash(parsed)


__all__ = ["CheckpointRepository"]
