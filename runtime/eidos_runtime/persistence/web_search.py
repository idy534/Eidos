from __future__ import annotations

import sqlite3

from eidos_runtime.db.database import Repository, now_ms
from eidos_runtime.db.events import append_event
from eidos_runtime.domain.web_search import (
    RunWebSearchSnapshot,
    StoredWebSearchSettings,
    WebSearchError,
)
from eidos_runtime.runtime.state_machine import EventType


class WebSearchRepository(Repository):
    def read(self) -> StoredWebSearchSettings:
        with self.lock:
            return self.read_in(self._connection())

    @staticmethod
    def read_in(connection: sqlite3.Connection) -> StoredWebSearchSettings:
        row = connection.execute(
            "SELECT settings_json FROM web_search_settings WHERE id=1"
        ).fetchone()
        return (
            StoredWebSearchSettings.model_validate_json(row[0])
            if row
            else StoredWebSearchSettings()
        )

    def save(self, value: StoredWebSearchSettings, expected: int) -> None:
        with self.database.transaction() as connection:
            if self.read_in(connection).revision != expected:
                raise WebSearchError(
                    "WEB_SEARCH_CONFIG_CONFLICT",
                    "网页搜索配置已经改变。请刷新后重新保存。",
                )
            if value.revision != expected + 1:
                raise WebSearchError("invalid_arguments", "网页搜索配置版本无效。")
            connection.execute(
                "INSERT INTO web_search_settings VALUES(1,?) ON CONFLICT(id) DO UPDATE SET settings_json=excluded.settings_json",
                (value.model_dump_json(),),
            )
            append_event(
                connection, EventType.WEB_SEARCH_CHANGED, now_ms(), {"id": "web-search"}
            )

    @classmethod
    def freeze(cls, connection: sqlite3.Connection, run_id: str) -> None:
        settings = cls.read_in(connection)
        provider = settings.config.default_provider
        reference = settings.credentials.get(provider) if provider else None
        if provider and reference:
            snapshot = RunWebSearchSnapshot(
                revision=settings.revision,
                provider=provider,
                config=settings.config,
                credential_ref=reference,
            )
            connection.execute(
                "INSERT INTO run_web_search_snapshots VALUES(?,?)",
                (run_id, snapshot.model_dump_json()),
            )

    @staticmethod
    def inherit(
        connection: sqlite3.Connection, run_id: str, parent_run_id: str
    ) -> None:
        connection.execute(
            "DELETE FROM run_web_search_snapshots WHERE run_id=?", (run_id,)
        )
        connection.execute(
            "INSERT INTO run_web_search_snapshots SELECT ?,snapshot_json FROM run_web_search_snapshots WHERE run_id=?",
            (run_id, parent_run_id),
        )

    def snapshot(self, run_id: str) -> RunWebSearchSnapshot | None:
        with self.lock:
            row = (
                self._connection()
                .execute(
                    "SELECT snapshot_json FROM run_web_search_snapshots WHERE run_id=?",
                    (run_id,),
                )
                .fetchone()
            )
        return RunWebSearchSnapshot.model_validate_json(row[0]) if row else None

    def credential_retained(self, reference: str) -> bool:
        with self.lock:
            current = self.read()
            if reference in {
                current.credentials.deepseek,
                current.credentials.langsearch,
            }:
                return True
            return (
                self._connection()
                .execute(
                    "SELECT 1 FROM run_web_search_snapshots s JOIN runs r ON r.id=s.run_id "
                    "WHERE r.status IN ('queued','running','waiting_approval','waiting_input','waiting_agents','finalizing') "
                    "AND json_extract(s.snapshot_json,'$.credential_ref')=? LIMIT 1",
                    (reference,),
                )
                .fetchone()
                is not None
            )
