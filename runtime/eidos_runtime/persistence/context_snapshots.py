from __future__ import annotations

import json

from pydantic import ValidationError

from eidos_runtime.memory.context import context_epochs
from eidos_runtime.memory.repository import MemoryRejected
from eidos_runtime.context.plan import ContextSnapshot
from eidos_runtime.db.database import Database, Repository
from eidos_runtime.persistence.errors import PersistenceCorruptionError
from eidos_runtime.db.json_blobs import (
    JsonBlobCorruptionError,
    JsonBlobReference,
    JsonBlobStore,
)
from eidos_runtime.repo_intelligence.retrieval import RetrievalSnapshot


class ContextSnapshotRepository(Repository):
    """Persist immutable retrieval, plan and exact model-request snapshots."""

    def __init__(
        self,
        database: Database,
        *,
        blobs: JsonBlobStore | None = None,
    ) -> None:
        super().__init__(database)
        if blobs is None:
            blobs = database.json_blobs
        self.blobs = blobs

    def persist(
        self,
        *,
        run_id: str,
        retrieval: RetrievalSnapshot | None,
        snapshot: ContextSnapshot,
    ) -> ContextSnapshot:
        plan = snapshot.plan
        if snapshot.plan_id != plan.plan_id:
            raise ValueError("context persistence snapshot lineage mismatch")
        if retrieval is None:
            if plan.retrieval_snapshot_id is not None:
                raise ValueError("context persistence snapshot lineage mismatch")
        elif (
            plan.retrieval_snapshot_id != retrieval.snapshot_id
            or plan.inventory_snapshot_id != retrieval.inventory_snapshot_id
            or plan.index_snapshot_id != retrieval.index_snapshot_id
        ):
            raise ValueError("context persistence snapshot lineage mismatch")
        with self.lock, self.blobs.lock, self._connection() as connection:
            epochs = context_epochs(snapshot.model_context)
            if not self.database.memory.valid_epochs(connection, epochs):
                raise MemoryRejected("memory_snapshot_revoked")
            stored_snapshot = self._store_snapshot(snapshot)
            if retrieval is not None:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO repository_retrieval_snapshots (
                        id, inventory_snapshot_id, index_snapshot_id,
                        snapshot_hash, snapshot_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        retrieval.snapshot_id,
                        retrieval.inventory_snapshot_id,
                        retrieval.index_snapshot_id,
                        retrieval.snapshot_hash,
                        retrieval.model_dump_json(),
                        retrieval.created_at_ms,
                    ),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO run_repository_retrievals (
                        run_id, retrieval_snapshot_id, created_at
                    ) VALUES (?, ?, ?)
                    """,
                    (run_id, retrieval.snapshot_id, snapshot.created_at_ms),
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
                    plan.plan_id,
                    run_id,
                    plan.retrieval_snapshot_id,
                    plan.model_profile_snapshot_hash,
                    plan.rule_resolution_snapshot_id,
                    plan.inventory_snapshot_id,
                    plan.index_snapshot_id,
                    plan.snapshot_hash,
                    plan.model_dump_json(),
                    plan.created_at_ms,
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
                    snapshot.snapshot_id,
                    run_id,
                    snapshot.model_attempt_id,
                    snapshot.plan_id,
                    snapshot.snapshot_hash,
                    stored_snapshot,
                    snapshot.created_at_ms,
                ),
            )
            for scope, epoch in epochs.items():
                connection.execute("INSERT OR IGNORE INTO memory_snapshot_refs(snapshot_id,scope_id,privacy_epoch) VALUES(?,?,?)",
                                   (snapshot.snapshot_id, scope, epoch))
        return self.read(snapshot.snapshot_id)

    def read(self, snapshot_id: str) -> ContextSnapshot:
        with self.lock:
            row = (
                self._connection()
                .execute(
                    "SELECT snapshot_json, memory_revoked FROM context_snapshots WHERE id = ?",
                    (snapshot_id,),
                )
                .fetchone()
            )
        if row is None:
            raise LookupError("context snapshot not found")
        if row["memory_revoked"]:
            raise MemoryRejected("memory_snapshot_revoked")
        try:
            return self._decode_snapshot(row["snapshot_json"])
        except (TypeError, ValidationError, ValueError, JsonBlobCorruptionError):
            raise PersistenceCorruptionError(
                "persistence_record_invalid", record="context_snapshot"
            ) from None

    def read_for_model_attempt(self, model_attempt_id: str) -> ContextSnapshot | None:
        with self.lock:
            row = (
                self._connection()
                .execute(
                    """
                SELECT context_snapshots.snapshot_json, context_snapshots.memory_revoked
                FROM context_snapshots
                LEFT JOIN model_attempts
                  ON model_attempts.context_snapshot_id = context_snapshots.id
                WHERE context_snapshots.model_attempt_id = ?
                   OR model_attempts.id = ?
                LIMIT 1
                """,
                    (model_attempt_id, model_attempt_id),
                )
                .fetchone()
            )
        if row is None:
            return None
        if row["memory_revoked"]:
            raise MemoryRejected("memory_snapshot_revoked")
        try:
            return self._decode_snapshot(row["snapshot_json"])
        except (TypeError, ValidationError, ValueError, JsonBlobCorruptionError):
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
                raise ValueError(
                    "running model attempt does not match context snapshot"
                )
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
                if (
                    current is None
                    or current["context_snapshot_id"] != snapshot.snapshot_id
                ):
                    raise ValueError("model attempt context snapshot is immutable")
        return persisted

    def read_running_for_run(self, run_id: str) -> ContextSnapshot | None:
        with self.lock:
            row = (
                self._connection()
                .execute(
                    """
                SELECT context_snapshots.snapshot_json, context_snapshots.memory_revoked FROM model_attempts
                JOIN steps ON steps.id = model_attempts.step_id
                JOIN context_snapshots
                  ON context_snapshots.id = model_attempts.context_snapshot_id
                WHERE steps.run_id = ? AND model_attempts.status = 'running'
                ORDER BY model_attempts.creation_seq DESC LIMIT 1
                """,
                    (run_id,),
                )
                .fetchone()
            )
        if row is None:
            return None
        if row["memory_revoked"]:
            raise MemoryRejected("memory_snapshot_revoked")
        try:
            return self._decode_snapshot(row["snapshot_json"])
        except (TypeError, ValidationError, ValueError, JsonBlobCorruptionError):
            raise PersistenceCorruptionError(
                "persistence_record_invalid", record="context_snapshot"
            ) from None

    def read_latest_for_run(self, run_id: str) -> ContextSnapshot | None:
        """Return the latest exact model-request projection for a Run."""

        with self.lock:
            row = (
                self._connection()
                .execute(
                    """
                SELECT snapshot_json, memory_revoked FROM context_snapshots
                WHERE run_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                    (run_id,),
                )
                .fetchone()
            )
        if row is None:
            return None
        if row["memory_revoked"]:
            raise MemoryRejected("memory_snapshot_revoked")
        try:
            return self._decode_snapshot(row["snapshot_json"])
        except (TypeError, ValidationError, ValueError, JsonBlobCorruptionError):
            raise PersistenceCorruptionError(
                "persistence_record_invalid", record="context_snapshot"
            ) from None

    def _store_snapshot(self, snapshot: ContextSnapshot) -> str:
        """Share immutable request items across attempts without changing their hash."""
        payload = snapshot.model_dump(mode="json")
        context = payload.pop("model_context")
        tools = payload.pop("tool_definitions")
        manifest = {
            "$eidosContextBlocks": 1,
            "snapshot": payload,
            "modelContextRefs": [
                self.blobs.put_json(
                    "context-item", json.dumps(item, ensure_ascii=False, sort_keys=True)
                )
                for item in context
            ],
            "toolDefinitionsRef": self.blobs.put_json(
                "context-tools", json.dumps(tools, ensure_ascii=False, sort_keys=True)
            ),
        }
        return self.blobs.put_json(
            "context-snapshot-v2", json.dumps(manifest, ensure_ascii=False)
        )

    def _decode_snapshot(self, stored: str) -> ContextSnapshot:
        reference = JsonBlobReference.from_json(stored)
        if reference is None or reference.kind == "context-snapshot":
            return ContextSnapshot.model_validate_json(
                self.blobs.read_json(stored, expected_kind="context-snapshot")
            )
        if reference.kind != "context-snapshot-v2":
            raise JsonBlobCorruptionError("context snapshot kind is invalid")
        manifest = json.loads(self.blobs.read(reference))
        if (
            not isinstance(manifest, dict)
            or set(manifest)
            != {
                "$eidosContextBlocks",
                "snapshot",
                "modelContextRefs",
                "toolDefinitionsRef",
            }
            or manifest["$eidosContextBlocks"] != 1
        ):
            raise JsonBlobCorruptionError("context manifest is invalid")
        payload = manifest["snapshot"]
        refs = manifest["modelContextRefs"]
        if (
            not isinstance(payload, dict)
            or not isinstance(refs, list)
            or len(refs) > 10000
        ):
            raise JsonBlobCorruptionError("context manifest is invalid")
        payload["model_context"] = [
            json.loads(self.blobs.read_json(value, expected_kind="context-item"))
            for value in refs
        ]
        payload["tool_definitions"] = json.loads(
            self.blobs.read_json(
                manifest["toolDefinitionsRef"], expected_kind="context-tools"
            )
        )
        return ContextSnapshot.model_validate_json(
            json.dumps(payload, ensure_ascii=False)
        )


__all__ = ["ContextSnapshotRepository"]
