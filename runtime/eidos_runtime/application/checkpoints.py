from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from uuid import uuid4

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.worktree_snapshots import WorktreeSnapshotService
from eidos_runtime.db.database import Database, now_ms
from eidos_runtime.db.repositories.sessions import SessionRepository
from eidos_runtime.git.errors import GitError, WorktreeError
from eidos_runtime.git.manager import WorktreeManager
from eidos_runtime.persistence.checkpoints import (
    CheckpointActionRecord,
    CheckpointRecord,
    CheckpointRepository,
)
from eidos_runtime.protocol.methods import (
    CheckpointDto,
    CheckpointListResponseDto,
    CheckpointRewindResponseDto,
)

logger = logging.getLogger("eidos.runtime")

_ACTIVE_RUN_STATUSES = (
    "queued",
    "running",
    "waiting_approval",
    "waiting_input",
    "waiting_agents",
    "finalizing",
)


class CheckpointApplication:
    """Capture and rewind lightweight Git-backed workspace checkpoints."""

    def __init__(
        self,
        database: Database,
        worktrees: WorktreeManager | None,
        snapshots: WorktreeSnapshotService | None,
        *,
        repository: CheckpointRepository | None = None,
    ) -> None:
        self._database = database
        self._worktrees = worktrees
        self._snapshots = snapshots
        self._repository = repository or CheckpointRepository(database)
        self._sessions = SessionRepository(database)

    def capture_run_checkpoint(self, run_id: str) -> CheckpointRecord | None:
        """Capture a Git working-tree checkpoint immediately before a Run runs."""

        if self._worktrees is None or self._snapshots is None:
            return None
        try:
            with self._database.lock:
                conn = self._database.connection()
                run_row = conn.execute(
                    "SELECT id, session_id, model_id FROM runs WHERE id = ?",
                    (run_id,),
                ).fetchone()
                if run_row is None:
                    return None
                session_id = str(run_row["session_id"])
                model_id = str(run_row["model_id"])
                ordinal_row = conn.execute(
                    "SELECT COALESCE(MAX(ordinal), 0) AS max_ordinal FROM items WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                item_ordinal = int(ordinal_row["max_ordinal"]) if ordinal_row is not None else 0

            session = self._sessions.read_session(session_id)
            if session is None or session.project_id is None:
                return None

            snapshot_id = f"chk-snap-{uuid4().hex[:16]}"
            if session.worktree_id is not None:
                worktree = self._worktrees.open(session.worktree_id)
                snapshot = self._snapshots.save(
                    worktree,
                    snapshot_id=snapshot_id,
                    replace_older=False,
                )
            else:
                project = self._worktrees.project(session.project_id)
                if project.git_common_dir is None:
                    return None
                root = Path(session.workspace_root)
                discovery = self._worktrees.discover(root)
                if discovery.git_common_dir != project.git_common_dir:
                    return None
                snapshot = self._snapshots.save_local(
                    project,
                    workspace_root=root,
                    session_id=session.id,
                    snapshot_id=snapshot_id,
                )

            workspace_hash = hashlib.sha256(
                str(Path(session.workspace_root).resolve()).encode("utf-8")
            ).hexdigest()
            model_hash = hashlib.sha256(model_id.encode("utf-8")).hexdigest()
            record = CheckpointRecord(
                id=f"chk-{uuid4().hex[:16]}",
                runId=run_id,
                itemOrdinal=item_ordinal,
                creationSeq=0,
                workspaceIdentityHash=workspace_hash,
                gitHead=snapshot.head,
                gitSnapshotId=snapshot.id,
                modelProfileSnapshotHash=model_hash,
                reconciliationRequired=False,
                createdAt=now_ms(),
            )
            return self._repository.create(record)
        except Exception:
            logger.warning(
                "checkpoint_capture_skipped",
                extra={"run_id": run_id},
                exc_info=logger.isEnabledFor(logging.DEBUG),
            )
            return None

    def release_for_session(self, session_id: str) -> None:
        """Drop a Session's checkpoints and the snapshots only they referenced.

        Checkpoint snapshots carry the Session id under a restrictive foreign
        key, so they must go before the Session row. Anchor or artifact cleanup
        that cannot complete is logged and left for storage reconciliation.
        """

        snapshot_ids = self._repository.delete_for_session(session_id)
        if self._snapshots is None:
            return
        for snapshot_id in snapshot_ids:
            snapshot = self._snapshots.snapshots.read(snapshot_id)
            if snapshot is None:
                continue
            if (
                self._snapshots.snapshots.referenced_by_checkpoint(snapshot_id)
                or self._snapshots.snapshots.referenced_by_lifecycle(snapshot_id)
            ):
                continue
            try:
                self._snapshots.delete_anchor_if_expected(snapshot)
                self._snapshots.artifacts.delete(snapshot.artifact_path)
                self._snapshots.snapshots.delete(snapshot_id)
            except Exception:
                logger.warning(
                    "checkpoint_snapshot_cleanup_deferred",
                    extra={"session_id": session_id, "snapshot_id": snapshot_id},
                    exc_info=logger.isEnabledFor(logging.DEBUG),
                )

    def list_checkpoints(self, session_id: str) -> CheckpointListResponseDto:
        if self._sessions.read_session(session_id) is None:
            raise ApplicationError("SESSION_NOT_FOUND")
        records = self._repository.list_for_session(session_id)
        return CheckpointListResponseDto(
            checkpoints=[_to_dto(record) for record in records]
        )

    def rewind_checkpoint(
        self,
        session_id: str,
        checkpoint_id: str,
        *,
        operation_id: str | None = None,
    ) -> CheckpointRewindResponseDto:
        session = self._sessions.read_session(session_id)
        if session is None:
            raise ApplicationError("SESSION_NOT_FOUND")

        with self._database.lock:
            conn = self._database.connection()
            placeholders = ", ".join("?" for _ in _ACTIVE_RUN_STATUSES)
            active_row = conn.execute(
                f"SELECT 1 FROM runs WHERE session_id = ? AND status IN ({placeholders}) LIMIT 1",
                (session_id, *_ACTIVE_RUN_STATUSES),
            ).fetchone()
            if active_row is not None:
                raise ApplicationError("SESSION_ACTIVE")
            belonging_row = conn.execute(
                """
                SELECT c.id FROM checkpoints c
                JOIN runs r ON r.id = c.run_id
                WHERE c.id = ? AND r.session_id = ?
                """,
                (checkpoint_id, session_id),
            ).fetchone()
            latest_run_row = conn.execute(
                """
                SELECT id FROM runs
                WHERE session_id = ?
                ORDER BY creation_seq DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()

        if belonging_row is None:
            raise ApplicationError("CHECKPOINT_NOT_FOUND")

        checkpoint = self._repository.read(checkpoint_id)
        if checkpoint is None or checkpoint.git_snapshot_id is None:
            raise ApplicationError("CHECKPOINT_SNAPSHOT_UNAVAILABLE")

        if self._worktrees is None or self._snapshots is None:
            raise ApplicationError("CHECKPOINT_SNAPSHOT_UNAVAILABLE")

        snapshot = self._snapshots.snapshots.read(checkpoint.git_snapshot_id)
        if snapshot is None:
            raise ApplicationError("CHECKPOINT_SNAPSHOT_UNAVAILABLE")

        try:
            self._snapshots.verify(snapshot)
            changes = self._snapshots.read_changes(snapshot)
            if session.worktree_id is not None:
                self._worktrees.restore_snapshot_state(
                    session.worktree_id,
                    head=snapshot.head,
                    changes=changes,
                    expected_fingerprint=snapshot.source_fingerprint,
                )
            else:
                root = Path(session.workspace_root)
                self._worktrees.git.restore_snapshot_state(
                    root, snapshot.head, changes
                )
                if self._worktrees.git.head(root) != snapshot.head:
                    raise ApplicationError("CHECKPOINT_REWIND_FAILED")
        except ApplicationError:
            raise
        except (GitError, WorktreeError) as error:
            raise ApplicationError("CHECKPOINT_REWIND_FAILED", str(error)) from error

        restored_at = now_ms()
        target_run_id = (
            str(latest_run_row["id"])
            if latest_run_row is not None
            else checkpoint.run_id
        )
        action_id = operation_id or f"chk-act-{uuid4().hex[:16]}"
        self._repository.record_action(
            CheckpointActionRecord(
                id=action_id,
                checkpointId=checkpoint.id,
                action="rewind",
                sourceRunId=checkpoint.run_id,
                targetRunId=target_run_id,
                createdAt=restored_at,
            )
        )
        return CheckpointRewindResponseDto(
            checkpointId=checkpoint.id,
            sessionId=session.id,
            runId=checkpoint.run_id,
            gitHead=snapshot.head,
            restoredAt=restored_at,
        )


def _to_dto(record: CheckpointRecord) -> CheckpointDto:
    return CheckpointDto(
        id=record.id,
        runId=record.run_id,
        itemOrdinal=record.item_ordinal,
        creationSeq=record.creation_seq,
        workspaceIdentityHash=record.workspace_identity_hash,
        gitHead=record.git_head,
        gitSnapshotId=record.git_snapshot_id,
        reconciliationRequired=record.reconciliation_required,
        createdAt=record.created_at,
    )
