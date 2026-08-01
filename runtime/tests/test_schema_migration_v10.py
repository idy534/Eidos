from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.db.schema import SCHEMA_VERSION, V9_SCHEMA_SQL  # noqa: E402
from eidos_runtime.db.storage import DATABASE_NAME, SessionStore  # noqa: E402
from eidos_runtime.runtime.fault_injection import injected_faults  # noqa: E402


class _OneShotFault:
    def __init__(self, point: str) -> None:
        self.point = point

    def hit(self, point: str) -> None:
        if point == self.point:
            raise sqlite3.OperationalError(f"injected {point}")


class SchemaV10MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-v10-migration-")
        self.data = Path(self.temporary.name) / "data"
        self.data.mkdir(mode=0o700)
        self.database = self.data / DATABASE_NAME

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _create_v9(self, *, with_facts: bool = False) -> None:
        connection = sqlite3.connect(self.database)
        connection.executescript(V9_SCHEMA_SQL)
        if with_facts:
            connection.execute(
                "INSERT INTO sessions (id, workspace_root, created_at, updated_at) "
                "VALUES ('session', '/workspace', 1, 1)"
            )
            connection.execute(
                """
                INSERT INTO runs (
                    id, session_id, user_input, model_profile_json, status,
                    created_at, updated_at
                ) VALUES ('run', 'session', 'goal', '{}', 'succeeded', 2, 2)
                """
            )
            connection.execute(
                """
                INSERT INTO items (
                    id, session_id, run_id, ordinal, kind, status, created_at
                ) VALUES (
                    'item', 'session', 'run', 1, 'tool_call', 'completed', 3
                )
                """
            )
            connection.execute(
                """
                INSERT INTO tool_calls (
                    id, item_id, model_step_index, batch_order,
                    provider_call_id, tool_name, status, arguments_json,
                    started_at, completed_at
                ) VALUES (
                    'tool', 'item', 1, 0, 'provider-tool', 'read_file',
                    'completed', '{}', 4, 5
                )
                """
            )
            connection.execute(
                """
                INSERT INTO approvals (
                    id, tool_call_id, run_id, item_id, status,
                    request_hash, request_json, decision, created_at, decided_at
                ) VALUES (
                    'approval', 'tool', 'run', 'item', 'approved',
                    'hash', '{}', 'approved', 4, 5
                )
                """
            )
            cursor = connection.execute(
                """
                INSERT INTO events (
                    event_contract_version, event_type, occurred_at,
                    session_id, run_id, payload_json
                ) VALUES (1, 'RUN_UPDATED', 5, 'session', 'run', '{}')
                """
            )
            connection.execute(
                "INSERT INTO event_outbox (event_id, status) VALUES (?, 'pending')",
                (cursor.lastrowid,),
            )
        connection.execute("PRAGMA user_version = 9")
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

    def test_realistic_v9_database_upgrades_and_preserves_facts(self) -> None:
        self._create_v9(with_facts=True)

        store = SessionStore(self.data)
        store.initialize()

        self.assertEqual(store.health(), {"state": "ready"})
        assert store.connection is not None
        self.assertEqual(
            store.connection.execute("PRAGMA user_version").fetchone()[0],
            SCHEMA_VERSION,
        )
        for table in (
            "sessions", "runs", "items", "tool_calls", "approvals", "events",
            "event_outbox",
        ):
            self.assertEqual(
                store.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],
                1,
            )
        self.assertIsNotNone(
            store.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'repository_fts'"
            ).fetchone()
        )
        store.close()

    def test_table_failure_rolls_back_to_intact_v9(self) -> None:
        self._create_v9(with_facts=True)

        with injected_faults(_OneShotFault("migration_v10_after_first_table")):
            store = SessionStore(self.data)
            store.initialize()

        self.assertEqual(
            store.health(),
            {"state": "health_only", "code": "schema_migration_failed"},
        )
        revision, tables = self._revision_and_tables()
        self.assertEqual(revision, 9)
        self.assertNotIn("repository_snapshots", tables)
        check = sqlite3.connect(self.database)
        self.assertEqual(check.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1)
        check.close()

    def test_pragma_failure_rolls_back_to_intact_v9(self) -> None:
        self._create_v9()

        with injected_faults(_OneShotFault("migration_v10_after_user_version")):
            store = SessionStore(self.data)
            store.initialize()

        self.assertEqual(
            store.health(),
            {"state": "health_only", "code": "schema_migration_failed"},
        )
        revision, tables = self._revision_and_tables()
        self.assertEqual(revision, 9)
        self.assertNotIn("repository_snapshots", tables)

    def test_restart_after_interrupted_migration_converges(self) -> None:
        self._create_v9()
        connection = sqlite3.connect(self.database)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("CREATE TABLE repository_snapshots_interrupted (id TEXT)")
        connection.close()

        store = SessionStore(self.data)
        store.initialize()

        self.assertEqual(store.health(), {"state": "ready"})
        revision, tables = self._revision_and_tables()
        self.assertEqual(revision, SCHEMA_VERSION)
        self.assertNotIn("repository_snapshots_interrupted", tables)
        self.assertIn("repository_snapshots", tables)
        store.close()

    def test_fts5_unavailable_rolls_back_to_v9(self) -> None:
        self._create_v9()

        with patch(
            "eidos_runtime.db.migrations.v009_to_v010.verify_fts5_capability",
            side_effect=RuntimeError("fts5 unavailable"),
        ):
            store = SessionStore(self.data)
            store.initialize()

        self.assertEqual(
            store.health(),
            {"state": "health_only", "code": "schema_migration_failed"},
        )
        revision, tables = self._revision_and_tables()
        self.assertEqual(revision, 9)
        self.assertNotIn("repository_fts", tables)

    def test_v8_and_v11_are_rejected_without_mutation(self) -> None:
        for revision in (8, 11):
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
