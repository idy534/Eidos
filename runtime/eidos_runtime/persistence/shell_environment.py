from __future__ import annotations

from eidos_runtime.db.database import Repository, now_ms
from eidos_runtime.db.events import append_event
from eidos_runtime.domain.shell_environment import (
    ShellEnvironmentPolicy,
    ShellEnvironmentSettings,
    ShellEnvironmentConfigConflict,
)
from eidos_runtime.runtime.state_machine import EventType


class ShellEnvironmentRepository(Repository):
    def read(self) -> ShellEnvironmentSettings:
        with self.lock:
            row = (
                self._connection()
                .execute(
                    "SELECT revision, inherit FROM shell_environment_settings WHERE id=1"
                )
                .fetchone()
            )
            if row is None:
                return ShellEnvironmentSettings()
            return ShellEnvironmentSettings(
                revision=row["revision"],
                policy=ShellEnvironmentPolicy(inherit=row["inherit"]),
            )

    def save(
        self, policy: ShellEnvironmentPolicy, expected_revision: int
    ) -> ShellEnvironmentSettings:
        with self.lock, self.database.transaction() as connection:
            current = self.read()
            if current.revision != expected_revision:
                raise ShellEnvironmentConfigConflict(
                    "沙盒环境设置已经改变，请重新读取后保存。"
                )
            saved = ShellEnvironmentSettings(
                revision=current.revision + 1, policy=policy
            )
            connection.execute(
                "INSERT INTO shell_environment_settings VALUES(1,?,?) "
                "ON CONFLICT(id) DO UPDATE SET revision=excluded.revision, inherit=excluded.inherit",
                (saved.revision, policy.inherit),
            )
            append_event(
                connection,
                EventType.SHELL_ENVIRONMENT_CHANGED,
                now_ms(),
                {"id": "shell-environment"},
            )
            return saved
