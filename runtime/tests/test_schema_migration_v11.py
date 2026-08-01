from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.db.schema import (  # noqa: E402
    SCHEMA_VERSION,
    V10_BASE_SCHEMA_SQL,
    V10_CONTEXT_SCHEMA_SQL,
    V10_REPOSITORY_SCHEMA_SQL,
)
from eidos_runtime.db.storage import DATABASE_NAME, SessionStore  # noqa: E402


class SchemaV11MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-v11-migration-")
        self.data = Path(self.temporary.name) / "data"
        self.data.mkdir(mode=0o700)
        self.database = self.data / DATABASE_NAME

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _create_v10(self, *, with_facts: bool = False) -> None:
        connection = sqlite3.connect(self.database)
        connection.executescript(
            V10_BASE_SCHEMA_SQL
            + V10_REPOSITORY_SCHEMA_SQL
            + V10_CONTEXT_SCHEMA_SQL
        )
        if with_facts:
            model_config = {
                "provider": "deepseek",
                "model_id": "deepseek-v4-flash",
                "wire_api": "openai_chat_completions",
                "request_timeout": 30.0,
            }
            connection.execute(
                "INSERT INTO sessions (id, workspace_root, created_at, updated_at) "
                "VALUES ('session', '/workspace', 1, 1)"
            )
            connection.execute(
                """
                INSERT INTO runs (
                    id, session_id, user_input, model_id, model_profile_json,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'succeeded', 2, 2)
                """,
                (
                    "run",
                    "session",
                    "goal",
                    "deepseek-v4-flash",
                    json.dumps(model_config),
                ),
            )
            connection.execute(
                "INSERT INTO model_profiles "
                "(id, name, profile_json, created_at, updated_at) "
                "VALUES ('profile', 'Legacy', '{}', 1, 1)"
            )
            connection.execute(
                "INSERT INTO model_capability_snapshots "
                "(id, profile_id, snapshot_json, probed_at) "
                "VALUES ('capability', 'profile', '{}', 1)"
            )
            connection.execute(
                "INSERT INTO run_model_snapshots "
                "(run_id, profile_id, capability_snapshot_id, snapshot_json, frozen_at) "
                "VALUES ('run', 'profile', 'capability', '{}', 2)"
            )
        connection.execute("PRAGMA user_version = 10")
        connection.commit()
        connection.close()
        os.chmod(self.database, 0o600)

    def _revision_and_tables(self) -> tuple[int, set[str]]:
        connection = sqlite3.connect(self.database)
        try:
            revision = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            return revision, tables
        finally:
            connection.close()

    def test_v10_upgrades_preserves_runs_and_drops_legacy_model_storage(self) -> None:
        self._create_v10(with_facts=True)

        store = SessionStore(self.data)
        store.initialize()

        self.assertEqual(store.health(), {"state": "ready"})
        assert store.connection is not None
        self.assertEqual(
            store.connection.execute("PRAGMA user_version").fetchone()[0],
            SCHEMA_VERSION,
        )
        self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1)
        columns = {
            row[1] for row in store.connection.execute("PRAGMA table_info(runs)")
        }
        self.assertNotIn("model_profile_id", columns)
        _, tables = self._revision_and_tables()
        self.assertTrue({
            "model_profiles", "model_capability_snapshots", "run_model_snapshots"
        }.isdisjoint(tables))
        store.close()

    def test_failure_rolls_back_to_intact_v10(self) -> None:
        self._create_v10(with_facts=True)

        def fail_after_first_drop(connection: sqlite3.Connection) -> None:
            connection.execute("DROP TABLE run_model_snapshots")
            raise sqlite3.OperationalError("injected v11 migration failure")

        with patch(
            "eidos_runtime.db.migrations.v010_to_v011.migrate",
            side_effect=fail_after_first_drop,
        ):
            store = SessionStore(self.data)
            store.initialize()

        self.assertEqual(
            store.health(),
            {"state": "health_only", "code": "schema_migration_failed"},
        )
        revision, tables = self._revision_and_tables()
        self.assertEqual(revision, 10)
        self.assertIn("run_model_snapshots", tables)

    def test_other_revisions_are_rejected_without_mutation(self) -> None:
        for revision in (9, 12):
            with self.subTest(revision=revision):
                if self.database.exists():
                    self.database.unlink()
                connection = sqlite3.connect(self.database)
                connection.execute("CREATE TABLE legacy_marker (value TEXT)")
                connection.execute(f"PRAGMA user_version = {revision}")
                connection.commit()
                connection.close()
                os.chmod(self.database, 0o600)

                store = SessionStore(self.data)
                store.initialize()
                self.assertEqual(
                    store.health(),
                    {"state": "health_only", "code": "schema_revision_unsupported"},
                )
                actual_revision, tables = self._revision_and_tables()
                self.assertEqual(actual_revision, revision)
                self.assertEqual(tables, {"legacy_marker"})
                store.close()


if __name__ == "__main__":
    unittest.main()
