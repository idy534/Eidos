"""Schema migration tests for the supported V11 → V12 → V13 → V14 → V15 → V16 → V17 window.

V11: drops legacy model storage tables.
V12: adds effective_cwd to runs; adds resolved_instructions_hash and
     effective_cwd to step_resolution_snapshots.
V13: adds response feedback and run revision persistence.
V14: persists structured compaction-summary metadata.
V15: adds Runtime-owned Project and Worktree persistence.
V16: binds Sessions to Runtime-owned Worktrees while retaining legacy NULLs.
V17: adds durable managed Worktree lifecycle intents.
"""
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
    V11_BASE_SCHEMA_SQL,
    V15_WORKTREE_SCHEMA_SQL,
    V16_SESSION_WORKTREE_SCHEMA_SQL,
    V17_WORKTREE_LIFECYCLE_SCHEMA_SQL,
    SCHEMA_SQL,
)
from eidos_runtime.db.storage import DATABASE_NAME, SessionStore  # noqa: E402


class SchemaV11MigrationTests(unittest.TestCase):
    """Validate the current migration support window."""

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

    def _create_v11(self, *, with_facts: bool = False) -> None:
        connection = sqlite3.connect(self.database)
        connection.executescript(
            V11_BASE_SCHEMA_SQL
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
        connection.execute("PRAGMA user_version = 11")
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

    # ------------------------------------------------------------------
    # V10 is outside the current two-revision migration window.
    # ------------------------------------------------------------------

    def test_v10_is_rejected_without_mutation(self) -> None:
        self._create_v10(with_facts=True)

        store = SessionStore(self.data)
        store.initialize()

        self.assertEqual(
            store.health(),
            {"state": "health_only", "code": "schema_revision_unsupported"},
        )
        revision, tables = self._revision_and_tables()
        self.assertEqual(revision, 10)
        self.assertIn("run_model_snapshots", tables)
        store.close()

    # ------------------------------------------------------------------
    # V11 → V12 → V13 → V14 → V15 → V16 → V17 migration chain.
    # ------------------------------------------------------------------

    def test_v11_upgrades_to_v17_and_preserves_current_contract(self) -> None:
        self._create_v11(with_facts=True)

        store = SessionStore(self.data)
        store.initialize()

        self.assertEqual(store.health(), {"state": "ready"})
        assert store.connection is not None
        self.assertEqual(
            store.connection.execute("PRAGMA user_version").fetchone()[0],
            SCHEMA_VERSION,
        )
        columns = {
            row[1] for row in store.connection.execute("PRAGMA table_info(runs)")
        }
        self.assertIn("effective_cwd", columns)
        srs_columns = {
            row[1] for row in store.connection.execute(
                "PRAGMA table_info(step_resolution_snapshots)"
            )
        }
        self.assertIn("resolved_instructions_hash", srs_columns)
        self.assertIn("effective_cwd", srs_columns)
        compact_columns = {
            row[1] for row in store.connection.execute(
                "PRAGMA table_info(compact_summaries)"
            )
        }
        self.assertIn("summary_metadata_json", compact_columns)
        _, tables = self._revision_and_tables()
        self.assertIn("response_feedback", tables)
        self.assertIn("run_revisions", tables)
        self.assertIn("projects", tables)
        self.assertIn("worktrees", tables)
        self.assertIn("worktree_lifecycle_operations", tables)
        session_columns = {
            row[1]
            for row in store.connection.execute("PRAGMA table_info(sessions)")
        }
        self.assertIn("worktree_id", session_columns)
        self.assertIsNone(
            store.connection.execute(
                "SELECT worktree_id FROM sessions WHERE id = 'session'"
            ).fetchone()[0]
        )
        store.close()

    def test_v14_upgrades_to_v17_and_passes_integrity_checks(self) -> None:
        connection = sqlite3.connect(self.database)
        connection.executescript(
            SCHEMA_SQL
            .replace(V15_WORKTREE_SCHEMA_SQL, "", 1)
            .replace(V16_SESSION_WORKTREE_SCHEMA_SQL, "", 1)
            .replace(V17_WORKTREE_LIFECYCLE_SCHEMA_SQL, "", 1)
        )
        connection.execute("PRAGMA user_version = 14")
        connection.commit()
        connection.close()
        os.chmod(self.database, 0o600)

        store = SessionStore(self.data)
        store.initialize()

        self.assertEqual(store.health(), {"state": "ready"})
        assert store.connection is not None
        self.assertEqual(
            store.connection.execute("PRAGMA user_version").fetchone()[0],
            SCHEMA_VERSION,
        )
        self.assertEqual(
            store.connection.execute("PRAGMA integrity_check").fetchone()[0],
            "ok",
        )
        self.assertEqual(
            store.connection.execute("PRAGMA foreign_key_check").fetchall(), []
        )
        store.close()

    def test_v15_migration_failure_rolls_back_without_worktree_tables(self) -> None:
        connection = sqlite3.connect(self.database)
        connection.executescript(
            SCHEMA_SQL
            .replace(V15_WORKTREE_SCHEMA_SQL, "", 1)
            .replace(V16_SESSION_WORKTREE_SCHEMA_SQL, "", 1)
            .replace(V17_WORKTREE_LIFECYCLE_SCHEMA_SQL, "", 1)
        )
        connection.execute("PRAGMA user_version = 14")
        connection.commit()
        connection.close()
        os.chmod(self.database, 0o600)

        def fail_migration(connection: sqlite3.Connection) -> None:
            raise sqlite3.OperationalError("injected v15 migration failure")

        with patch(
            "eidos_runtime.db.migrations.v014_to_v015.migrate",
            side_effect=fail_migration,
        ):
            store = SessionStore(self.data)
            store.initialize()

        self.assertEqual(
            store.health(),
            {"state": "health_only", "code": "schema_migration_failed"},
        )
        revision, tables = self._revision_and_tables()
        self.assertEqual(revision, 14)
        self.assertNotIn("projects", tables)
        self.assertNotIn("worktrees", tables)

    def test_v16_migration_failure_rolls_back_without_session_binding(self) -> None:
        connection = sqlite3.connect(self.database)
        connection.executescript(
            SCHEMA_SQL
            .replace(V16_SESSION_WORKTREE_SCHEMA_SQL, "", 1)
            .replace(V17_WORKTREE_LIFECYCLE_SCHEMA_SQL, "", 1)
        )
        connection.execute("PRAGMA user_version = 15")
        connection.commit()
        connection.close()
        os.chmod(self.database, 0o600)

        def fail_migration(connection: sqlite3.Connection) -> None:
            raise sqlite3.OperationalError("injected v16 migration failure")

        with patch(
            "eidos_runtime.db.migrations.v015_to_v016.migrate",
            side_effect=fail_migration,
        ):
            store = SessionStore(self.data)
            store.initialize()

        self.assertEqual(
            store.health(),
            {"state": "health_only", "code": "schema_migration_failed"},
        )
        revision, _ = self._revision_and_tables()
        self.assertEqual(revision, 15)
        check = sqlite3.connect(self.database)
        self.assertNotIn(
            "worktree_id",
            {row[1] for row in check.execute("PRAGMA table_info(sessions)")},
        )
        check.close()

    def test_v17_migration_failure_rolls_back_without_lifecycle_table(self) -> None:
        connection = sqlite3.connect(self.database)
        connection.executescript(
            SCHEMA_SQL.replace(V17_WORKTREE_LIFECYCLE_SCHEMA_SQL, "", 1)
        )
        connection.execute("PRAGMA user_version = 16")
        connection.commit()
        connection.close()
        os.chmod(self.database, 0o600)

        def fail_migration(connection: sqlite3.Connection) -> None:
            raise sqlite3.OperationalError("injected v17 migration failure")

        with patch(
            "eidos_runtime.db.migrations.v016_to_v017.migrate",
            side_effect=fail_migration,
        ):
            store = SessionStore(self.data)
            store.initialize()

        self.assertEqual(
            store.health(),
            {"state": "health_only", "code": "schema_migration_failed"},
        )
        revision, tables = self._revision_and_tables()
        self.assertEqual(revision, 16)
        self.assertNotIn("worktree_lifecycle_operations", tables)
        store.close()

    def test_v12_migration_failure_rolls_back_to_intact_v11(self) -> None:
        self._create_v11(with_facts=True)

        def fail_on_v12(connection: sqlite3.Connection) -> None:
            raise sqlite3.OperationalError("injected v12 migration failure")

        with patch(
            "eidos_runtime.db.migrations.v011_to_v012.migrate",
            side_effect=fail_on_v12,
        ):
            store = SessionStore(self.data)
            store.initialize()

        self.assertEqual(
            store.health(),
            {"state": "health_only", "code": "schema_migration_failed"},
        )
        revision, _ = self._revision_and_tables()
        self.assertEqual(revision, 11)

    # ------------------------------------------------------------------
    # Unsupported revisions
    # ------------------------------------------------------------------

    def test_other_revisions_are_rejected_without_mutation(self) -> None:
        for revision in (9, SCHEMA_VERSION + 1):
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
