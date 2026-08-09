from __future__ import annotations

from pydantic import ValidationError

from eidos_runtime.context.plan import ContextSnapshot
from eidos_runtime.db.database import Repository
from eidos_runtime.persistence.errors import PersistenceCorruptionError
from eidos_runtime.repo_intelligence.retrieval import RetrievalSnapshot


class ContextSnapshotRepository(Repository):
    """Persist immutable retrieval, plan and exact model-request snapshots."""

    def persist(
        self,
        *,
        run_id: str,
        retrieval: RetrievalSnapshot,
        snapshot: ContextSnapshot,
    ) -> ContextSnapshot:
        plan = snapshot.plan
        if (
            plan.inventory_snapshot_id != retrieval.inventory_snapshot_id
            or plan.index_snapshot_id != retrieval.index_snapshot_id
            or snapshot.plan_id != plan.plan_id
        ):
            raise ValueError("context persistence snapshot lineage mismatch")
        with self.lock, self._connection() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO repository_retrieval_snapshots (
                    id, run_id, inventory_snapshot_id, index_snapshot_id,
                    snapshot_hash, snapshot_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    retrieval.snapshot_id, run_id, retrieval.inventory_snapshot_id,
                    retrieval.index_snapshot_id, retrieval.snapshot_hash,
                    retrieval.model_dump_json(), retrieval.created_at_ms,
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO context_plans (
                    id, run_id, retrieval_snapshot_id,
                    model_profile_snapshot_hash, rule_snapshot_id,
                    inventory_snapshot_id, index_snapshot_id, snapshot_hash,
                    plan_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan.plan_id, run_id, retrieval.snapshot_id,
                    plan.model_profile_snapshot_hash,
                    plan.rule_resolution_snapshot_id,
                    plan.inventory_snapshot_id, plan.index_snapshot_id,
                    plan.snapshot_hash, plan.model_dump_json(), plan.created_at_ms,
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO context_snapshots (
                    id, run_id, model_attempt_id, plan_id, snapshot_hash,
                    snapshot_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshot_id, run_id, snapshot.model_attempt_id,
                    snapshot.plan_id, snapshot.snapshot_hash,
                    snapshot.model_dump_json(), snapshot.created_at_ms,
                ),
            )
        return self.read(snapshot.snapshot_id)

    def read(self, snapshot_id: str) -> ContextSnapshot:
        with self.lock:
            row = self._connection().execute(
                "SELECT snapshot_json FROM context_snapshots WHERE id = ?",
                (snapshot_id,),
            ).fetchone()
        if row is None:
            raise LookupError("context snapshot not found")
        try:
            return ContextSnapshot.model_validate_json(row["snapshot_json"])
        except (TypeError, ValidationError, ValueError):
            raise PersistenceCorruptionError(
                "persistence_record_invalid", record="context_snapshot"
            ) from None

    def read_for_model_attempt(
        self, model_attempt_id: str
    ) -> ContextSnapshot | None:
        with self.lock:
            row = self._connection().execute(
                "SELECT snapshot_json FROM context_snapshots "
                "WHERE model_attempt_id = ?",
                (model_attempt_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            return ContextSnapshot.model_validate_json(row["snapshot_json"])
        except (TypeError, ValidationError, ValueError):
            raise PersistenceCorruptionError(
                "persistence_record_invalid", record="context_snapshot"
            ) from None

    def bind_running_attempt(
        self, run_id: str, snapshot: ContextSnapshot
    ) -> ContextSnapshot:
        persisted = self.read(snapshot.snapshot_id)
        with self.lock, self._connection() as connection:
            row = connection.execute(
                """
                SELECT model_attempts.id FROM model_attempts
                JOIN steps ON steps.id = model_attempts.step_id
                WHERE steps.run_id = ? AND model_attempts.status = 'running'
                ORDER BY model_attempts.creation_seq DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            if row is None or row["id"] != snapshot.model_attempt_id:
                raise ValueError("running model attempt does not match context snapshot")
            changed = connection.execute(
                """
                UPDATE model_attempts SET context_snapshot_id = ?
                WHERE id = ? AND context_snapshot_id IS NULL
                """,
                (snapshot.snapshot_id, snapshot.model_attempt_id),
            )
            if changed.rowcount != 1:
                current = connection.execute(
                    "SELECT context_snapshot_id FROM model_attempts WHERE id = ?",
                    (snapshot.model_attempt_id,),
                ).fetchone()
                if current is None or current["context_snapshot_id"] != snapshot.snapshot_id:
                    raise ValueError("model attempt context snapshot is immutable")
        return persisted

    def read_running_for_run(self, run_id: str) -> ContextSnapshot | None:
        with self.lock:
            row = self._connection().execute(
                """
                SELECT context_snapshots.snapshot_json FROM model_attempts
                JOIN steps ON steps.id = model_attempts.step_id
                JOIN context_snapshots
                  ON context_snapshots.id = model_attempts.context_snapshot_id
                WHERE steps.run_id = ? AND model_attempts.status = 'running'
                ORDER BY model_attempts.creation_seq DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            return ContextSnapshot.model_validate_json(row["snapshot_json"])
        except (TypeError, ValidationError, ValueError):
            raise PersistenceCorruptionError(
                "persistence_record_invalid", record="context_snapshot"
            ) from None

    def read_latest_for_run(self, run_id: str) -> ContextSnapshot | None:
        """Return the latest exact model-request projection for a Run."""

        with self.lock:
            row = self._connection().execute(
                """
                SELECT snapshot_json FROM context_snapshots
                WHERE run_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            return ContextSnapshot.model_validate_json(row["snapshot_json"])
        except (TypeError, ValidationError, ValueError):
            raise PersistenceCorruptionError(
                "persistence_record_invalid", record="context_snapshot"
            ) from None


__all__ = ["ContextSnapshotRepository"]
