from __future__ import annotations

from datetime import UTC, datetime

from eidos_runtime.db.database import Repository
from eidos_runtime.db.errors import StorageError
from eidos_runtime.domain.worktree_settings import WorktreeSettings


class WorktreeSettingsRepository(Repository):
    """Narrow persistence seam for the existing Runtime settings boundary."""

    def read(self) -> WorktreeSettings:
        with self.lock:
            row = self._connection().execute(
                "SELECT * FROM runtime_settings WHERE id = 1"
            ).fetchone()
        if row is None:
            raise StorageError("worktree_settings_missing")
        return WorktreeSettings.model_validate(
            {
                "automaticCleanup": bool(row["automatic_cleanup"]),
                "managedWorktreeLimit": int(row["managed_worktree_limit"]),
                "updatedAt": _timestamp(int(row["updated_at"])),
            }
        )

    def update(
        self,
        *,
        automatic_cleanup: bool,
        managed_worktree_limit: int,
    ) -> WorktreeSettings:
        if not 1 <= managed_worktree_limit <= 100:
            raise ValueError("managed Worktree limit must be between 1 and 100")
        now = _now_ms()
        with self.lock, self._connection() as connection:
            updated = connection.execute(
                """
                UPDATE runtime_settings
                SET automatic_cleanup = ?, managed_worktree_limit = ?, updated_at = ?
                WHERE id = 1
                """,
                (int(automatic_cleanup), managed_worktree_limit, now),
            )
            if updated.rowcount != 1:
                raise StorageError("worktree_settings_missing")
        return self.read()


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _timestamp(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=UTC)


__all__ = ["WorktreeSettingsRepository"]
