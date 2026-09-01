from __future__ import annotations

import sqlite3

from pydantic import ValidationError

from eidos_runtime.db.database import CommittedMutation, Repository
from eidos_runtime.db.errors import (
    ResourceNotFoundError,
    RuntimeDependencyBindingConflictError,
    RuntimeDependencySnapshotConflictError,
    StorageError,
)
from eidos_runtime.db.events import append_event
from eidos_runtime.models.runtime_dependency_records import (
    RuntimeDependencyBindingRecord,
    RuntimeDependencySnapshotRecord,
)
from eidos_runtime.runtime.state_machine import EventType


class RuntimeDependencyRepository(Repository):
    def persist_snapshot(
        self, record: RuntimeDependencySnapshotRecord
    ) -> CommittedMutation[RuntimeDependencySnapshotRecord]:
        with self.database.transaction() as connection:
            run = connection.execute(
                "SELECT id, session_id FROM runs WHERE id = ?",
                (record.run_id,),
            ).fetchone()
            if run is None:
                raise ResourceNotFoundError("run not found")
            existing = connection.execute(
                "SELECT * FROM run_dependency_snapshots WHERE run_id = ?",
                (record.run_id,),
            ).fetchone()
            if existing is not None:
                persisted = _snapshot_from_row(existing)
                if persisted != record:
                    raise RuntimeDependencySnapshotConflictError(
                        "runtime dependency snapshot is immutable"
                    )
                return CommittedMutation(persisted, ())
            connection.execute(
                """
                INSERT INTO run_dependency_snapshots (
                    run_id, manifest_hash, catalog_hash,
                    snapshot_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.manifest_hash,
                    record.catalog_hash,
                    record.snapshot_json,
                    record.created_at,
                ),
            )
            event = append_event(
                connection,
                EventType.RUN_UPDATED,
                record.created_at,
                {
                    "reason": "runtime_dependency_snapshot",
                    "manifestHash": record.manifest_hash,
                    "catalogHash": record.catalog_hash,
                },
                session_id=run["session_id"],
                run_id=record.run_id,
            )
            persisted = connection.execute(
                "SELECT * FROM run_dependency_snapshots WHERE run_id = ?",
                (record.run_id,),
            ).fetchone()
            assert persisted is not None
            return CommittedMutation(_snapshot_from_row(persisted), (event,))

    def read_snapshot(self, run_id: str) -> RuntimeDependencySnapshotRecord | None:
        with self.lock:
            row = self._connection().execute(
                "SELECT * FROM run_dependency_snapshots WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return _snapshot_from_row(row) if row is not None else None

    def persist_binding(
        self, record: RuntimeDependencyBindingRecord
    ) -> CommittedMutation[RuntimeDependencyBindingRecord]:
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM run_dependency_bindings WHERE binding_id = ?",
                (record.binding_id,),
            ).fetchone()
            if existing is not None:
                persisted = _binding_from_row(existing)
                if persisted != record:
                    raise RuntimeDependencyBindingConflictError(
                        "runtime dependency binding id is immutable"
                    )
                return CommittedMutation(persisted, ())

            run = connection.execute(
                "SELECT id, session_id FROM runs WHERE id = ?",
                (record.run_id,),
            ).fetchone()
            if run is None:
                raise ResourceNotFoundError("run not found")
            snapshot = connection.execute(
                """
                SELECT manifest_hash
                FROM run_dependency_snapshots
                WHERE run_id = ?
                """,
                (record.run_id,),
            ).fetchone()
            if snapshot is None:
                raise ResourceNotFoundError(
                    "runtime dependency snapshot not found"
                )
            if snapshot["manifest_hash"] != record.manifest_hash:
                raise RuntimeDependencySnapshotConflictError(
                    "runtime dependency binding references a different snapshot"
                )
            connection.execute(
                """
                INSERT INTO run_dependency_bindings (
                    run_id, binding_id, manifest_hash, requirements_hash,
                    qualified_skill_id, status, diagnostics_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.binding_id,
                    record.manifest_hash,
                    record.requirements_hash,
                    record.qualified_skill_id,
                    record.status,
                    record.diagnostics_json,
                    record.created_at,
                ),
            )
            payload: dict[str, object] = {
                "reason": (
                    "runtime_dependency_binding_invalid"
                    if record.status == "invalid"
                    else "runtime_dependency_binding"
                ),
                "bindingId": record.binding_id,
                "manifestHash": record.manifest_hash,
                "requirementsHash": record.requirements_hash,
            }
            payload["status"] = record.status
            if record.qualified_skill_id is not None:
                payload["qualifiedSkillId"] = record.qualified_skill_id
            event = append_event(
                connection,
                EventType.RUN_UPDATED,
                record.created_at,
                payload,
                session_id=run["session_id"],
                run_id=record.run_id,
            )
            persisted = connection.execute(
                "SELECT * FROM run_dependency_bindings WHERE binding_id = ?",
                (record.binding_id,),
            ).fetchone()
            assert persisted is not None
            return CommittedMutation(_binding_from_row(persisted), (event,))

    def read_binding(
        self, binding_id: str
    ) -> RuntimeDependencyBindingRecord | None:
        with self.lock:
            row = self._connection().execute(
                """
                SELECT * FROM run_dependency_bindings
                WHERE binding_id = ?
                """,
                (binding_id,),
            ).fetchone()
        return _binding_from_row(row) if row is not None else None

    def list_bindings(
        self, run_id: str
    ) -> tuple[RuntimeDependencyBindingRecord, ...]:
        with self.lock:
            rows = self._connection().execute(
                """
                SELECT * FROM run_dependency_bindings
                WHERE run_id = ?
                ORDER BY creation_seq ASC
                """,
                (run_id,),
            ).fetchall()
        return tuple(_binding_from_row(row) for row in rows)


def _snapshot_from_row(
    row: sqlite3.Row,
) -> RuntimeDependencySnapshotRecord:
    try:
        return RuntimeDependencySnapshotRecord(
            run_id=row["run_id"],
            manifest_hash=row["manifest_hash"],
            catalog_hash=row["catalog_hash"],
            snapshot_json=row["snapshot_json"],
            created_at=row["created_at"],
        )
    except ValidationError as error:
        raise StorageError("runtime dependency snapshot is invalid") from error


def _binding_from_row(row: sqlite3.Row) -> RuntimeDependencyBindingRecord:
    try:
        return RuntimeDependencyBindingRecord(
            run_id=row["run_id"],
            binding_id=row["binding_id"],
            manifest_hash=row["manifest_hash"],
            requirements_hash=row["requirements_hash"],
            qualified_skill_id=row["qualified_skill_id"],
            status=row["status"],
            diagnostics_json=row["diagnostics_json"],
            created_at=row["created_at"],
        )
    except ValidationError as error:
        raise StorageError("runtime dependency binding is invalid") from error


__all__ = ["RuntimeDependencyRepository"]
