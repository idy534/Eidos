from __future__ import annotations

import sqlite3

from eidos_runtime.db.database import Repository, now_ms
from eidos_runtime.db.events import append_event
from eidos_runtime.domain.guardrails import (
    BackgroundGuardrailPolicy,
    BackgroundGuardrailSettings,
    GuardrailConfigConflict,
)
from eidos_runtime.runtime.state_machine import EventType


class GuardrailSettingsRepository(Repository):
    @staticmethod
    def read_in(connection: sqlite3.Connection) -> BackgroundGuardrailSettings:
        try:
            row = connection.execute(
                "SELECT revision, max_tokens_per_cycle, max_cost_credits_per_cycle, max_idle_rounds "
                "FROM background_guardrail_settings WHERE id=1"
            ).fetchone()
        except sqlite3.OperationalError as error:
            if "no such table" in str(error).lower():
                return BackgroundGuardrailSettings()
            raise
        if row is None:
            return BackgroundGuardrailSettings()
        return BackgroundGuardrailSettings(
            revision=row["revision"],
            policy=BackgroundGuardrailPolicy(
                max_tokens_per_cycle=row["max_tokens_per_cycle"],
                max_cost_credits_per_cycle=row["max_cost_credits_per_cycle"],
                max_idle_rounds=row["max_idle_rounds"],
            ),
        )

    def read(self) -> BackgroundGuardrailSettings:
        with self.database.read_scope(), self.lock:
            return self.read_in(self._connection())

    def save(
        self, policy: BackgroundGuardrailPolicy, expected_revision: int
    ) -> BackgroundGuardrailSettings:
        with self.lock, self.database.transaction() as connection:
            current = self.read_in(connection)
            if current.revision != expected_revision:
                raise GuardrailConfigConflict("后台护栏设置已经改变，请重新读取后保存。")
            saved = BackgroundGuardrailSettings(
                revision=current.revision + 1, policy=policy
            )
            connection.execute(
                "INSERT INTO background_guardrail_settings VALUES(1,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET revision=excluded.revision, "
                "max_tokens_per_cycle=excluded.max_tokens_per_cycle, "
                "max_cost_credits_per_cycle=excluded.max_cost_credits_per_cycle, "
                "max_idle_rounds=excluded.max_idle_rounds",
                (
                    saved.revision,
                    policy.max_tokens_per_cycle,
                    policy.max_cost_credits_per_cycle,
                    policy.max_idle_rounds,
                ),
            )
            append_event(
                connection,
                EventType.GUARDRAILS_CHANGED,
                now_ms(),
                {"id": "guardrails"},
            )
            return saved
