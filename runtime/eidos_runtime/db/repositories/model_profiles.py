from __future__ import annotations

import sqlite3

from eidos_runtime.db.database import Repository
from eidos_runtime.db.errors import ResourceNotFoundError, StorageError
from eidos_runtime.model_gateway.models import (
    CapabilitySnapshot,
    ModelProfile,
    RunModelSnapshot,
)


class ModelProfileRepository(Repository):
    def create(self, profile: ModelProfile) -> ModelProfile:
        try:
            with self.lock, self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO model_profiles (
                        id, name, profile_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        profile.id,
                        profile.name,
                        profile.model_dump_json(),
                        int(profile.created_at.timestamp() * 1000),
                        int(profile.updated_at.timestamp() * 1000),
                    ),
                )
        except sqlite3.IntegrityError:
            raise ValueError("model profile already exists") from None
        return profile

    def update(self, profile: ModelProfile) -> ModelProfile:
        try:
            with self.lock, self._connection() as connection:
                updated = connection.execute(
                    """
                    UPDATE model_profiles
                    SET name = ?, profile_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        profile.name,
                        profile.model_dump_json(),
                        int(profile.updated_at.timestamp() * 1000),
                        profile.id,
                    ),
                )
        except sqlite3.IntegrityError:
            raise ValueError("model profile name already exists") from None
        if updated.rowcount != 1:
            raise ResourceNotFoundError("model profile not found")
        return profile

    def get(self, profile_id: str) -> ModelProfile | None:
        with self.lock:
            row = self._connection().execute(
                "SELECT profile_json FROM model_profiles WHERE id = ?",
                (profile_id,),
            ).fetchone()
        return self._profile(row["profile_json"]) if row is not None else None

    def list(self) -> list[ModelProfile]:
        with self.lock:
            rows = self._connection().execute(
                "SELECT profile_json FROM model_profiles ORDER BY name, id"
            ).fetchall()
        return [self._profile(row["profile_json"]) for row in rows]

    def delete(self, profile_id: str) -> None:
        with self.lock, self._connection() as connection:
            deleted = connection.execute(
                "DELETE FROM model_profiles WHERE id = ?",
                (profile_id,),
            )
        if deleted.rowcount != 1:
            raise ResourceNotFoundError("model profile not found")

    def save_capability(self, snapshot: CapabilitySnapshot) -> CapabilitySnapshot:
        with self.lock, self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO model_capability_snapshots (
                        id, profile_id, snapshot_json, probed_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        snapshot.id,
                        snapshot.profile_id,
                        snapshot.model_dump_json(),
                        int(snapshot.probed_at.timestamp() * 1000),
                    ),
                )
            except sqlite3.IntegrityError:
                raise ValueError("capability snapshot already exists") from None
        return snapshot

    def latest_capability(self, profile_id: str) -> CapabilitySnapshot | None:
        with self.lock:
            row = self._connection().execute(
                """
                SELECT snapshot_json FROM model_capability_snapshots
                WHERE profile_id = ?
                ORDER BY probed_at DESC, creation_seq DESC LIMIT 1
                """,
                (profile_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            return CapabilitySnapshot.model_validate_json(row["snapshot_json"])
        except (TypeError, ValueError):
            raise StorageError("model_capability_snapshot_invalid") from None

    def save_run_snapshot(
        self,
        run_id: str,
        snapshot: RunModelSnapshot,
    ) -> RunModelSnapshot:
        with self.lock, self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO run_model_snapshots (
                        run_id, profile_id, capability_snapshot_id,
                        snapshot_json, frozen_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        snapshot.profile.id,
                        snapshot.capability.id,
                        snapshot.model_dump_json(),
                        int(snapshot.frozen_at.timestamp() * 1000),
                    ),
                )
            except sqlite3.IntegrityError:
                raise ValueError("run model snapshot already exists or run is missing") from None
        return snapshot

    def read_run_snapshot(self, run_id: str) -> RunModelSnapshot:
        with self.lock:
            row = self._connection().execute(
                "SELECT snapshot_json FROM run_model_snapshots WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise ResourceNotFoundError("run model snapshot not found")
        try:
            return RunModelSnapshot.model_validate_json(row["snapshot_json"])
        except (TypeError, ValueError):
            raise StorageError("run_model_snapshot_invalid") from None

    @staticmethod
    def _profile(value: str) -> ModelProfile:
        try:
            return ModelProfile.model_validate_json(value)
        except (TypeError, ValueError):
            raise StorageError("model_profile_invalid") from None
