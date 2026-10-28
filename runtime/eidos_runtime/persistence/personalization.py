from __future__ import annotations

from eidos_runtime.db.database import Repository, now_ms
from eidos_runtime.db.events import append_event
from eidos_runtime.domain.personalization import (
    PersonalizationConflict,
    PersonalizationPreferences,
    PersonalizationSettings,
)
from eidos_runtime.runtime.state_machine import EventType


class PersonalizationRepository(Repository):
    def read(self) -> PersonalizationSettings:
        with self.lock:
            row = self._connection().execute(
                "SELECT revision, preferences_json FROM personalization_settings WHERE id=1"
            ).fetchone()
            if row is None:
                return PersonalizationSettings()
            return PersonalizationSettings(
                revision=row["revision"],
                preferences=PersonalizationPreferences.model_validate_json(row["preferences_json"]),
            )

    def save(self, preferences: PersonalizationPreferences, expected_revision: int) -> PersonalizationSettings:
        with self.lock, self.database.transaction() as connection:
            current = self.read()
            if current.revision != expected_revision:
                raise PersonalizationConflict("个性化设置已经改变。请保留当前内容，重新读取后再保存。")
            if preferences == current.preferences:
                return current
            saved = PersonalizationSettings(revision=current.revision + 1, preferences=preferences)
            connection.execute(
                "INSERT INTO personalization_settings VALUES(1,?,?) "
                "ON CONFLICT(id) DO UPDATE SET revision=excluded.revision, preferences_json=excluded.preferences_json",
                (saved.revision, preferences.model_dump_json()),
            )
            append_event(connection, EventType.PERSONALIZATION_CHANGED, now_ms(), {"id": "personalization"})
            return saved
