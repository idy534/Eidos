from __future__ import annotations

import json
import io
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.db.storage import (  # noqa: E402
    DATABASE_NAME,
    RESERVE_BYTES,
    RESERVE_NAME,
    SCHEMA_REVISION,
    SessionStore,
    StorageError,
)
from eidos_runtime.protocol.server import RuntimeServer  # noqa: E402


class PhaseTwoStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="eidos-storage-p2-")
        self.data_directory = Path(self.temporary_directory.name) / "data"
        self.data_directory.mkdir(mode=0o700)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_new_store_enables_required_pragmas_revision_and_reserve(self) -> None:
        store = SessionStore(self.data_directory)
        store.initialize()
        self.assertEqual(store.health(), {"state": "ready"})
        connection = store.connection
        assert connection is not None
        self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertGreaterEqual(connection.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
        self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_REVISION)
        reserve = (self.data_directory / RESERVE_NAME).stat()
        self.assertEqual(reserve.st_size, RESERVE_BYTES)
        self.assertGreaterEqual(reserve.st_blocks * 512, RESERVE_BYTES)
        store.close()

    def test_second_store_is_health_only_while_first_holds_lock(self) -> None:
        first = SessionStore(self.data_directory)
        first.initialize()
        second = SessionStore(self.data_directory)
        second.initialize()
        self.assertEqual(second.health(), {"state": "health_only", "code": "state_locked"})
        self.assertIsNone(second.connection)
        first.close()
        second.close()

    def test_second_server_exposes_health_but_rejects_business_requests(self) -> None:
        first = SessionStore(self.data_directory)
        first.initialize()
        output = io.StringIO()
        server = RuntimeServer(output, self.data_directory)
        server.handle({
            "jsonrpc": "2.0",
            "id": "client-init",
            "method": "initialize",
            "params": {
                "client": {"name": "test", "version": "1"},
                "protocolVersion": 1,
            },
        })
        server.handle({
            "jsonrpc": "2.0", "id": "client-health",
            "method": "runtime/health", "params": {},
        })
        server.handle({
            "jsonrpc": "2.0", "id": "client-list",
            "method": "session/list", "params": {},
        })
        messages = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(messages[1]["result"]["code"], "state_locked")
        self.assertEqual(
            messages[2]["error"]["data"]["code"], "STORAGE_HEALTH_ONLY"
        )
        server.close()
        first.close()

    def test_unknown_revision_does_not_create_business_tables(self) -> None:
        database = self.data_directory / DATABASE_NAME
        connection = sqlite3.connect(database)
        connection.execute("PRAGMA user_version = 999")
        connection.commit()
        connection.close()
        os.chmod(database, 0o600)

        store = SessionStore(self.data_directory)
        store.initialize()
        self.assertEqual(
            store.health(),
            {"state": "health_only", "code": "schema_revision_unsupported"},
        )
        check = sqlite3.connect(database)
        self.assertEqual(
            check.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),
            [],
        )
        check.close()

    def test_revision_one_migrates_with_verified_backup_and_keeps_session(self) -> None:
        workspace = Path(self.temporary_directory.name) / "workspace"
        workspace.mkdir()
        original = SessionStore(self.data_directory)
        original.initialize()
        session = original.create_session(str(workspace))
        assert original.connection is not None
        original.connection.execute("PRAGMA user_version = 1")
        original.connection.commit()
        original.close()

        migrated = SessionStore(self.data_directory)
        migrated.initialize()
        self.assertEqual(migrated.health(), {"state": "ready"})
        self.assertEqual(migrated.read_session(session["id"]), session)
        manifests = list(self.data_directory.glob("*.bak.json"))
        self.assertEqual(len(manifests), 1)
        manifest = json.loads(manifests[0].read_text(encoding="ascii"))
        backup = self.data_directory / manifest["backup"]
        self.assertTrue(backup.is_file())
        self.assertEqual(len(manifest["sha256"]), 64)
        migrated.close()

    def test_revision_two_adds_nullable_session_title(self) -> None:
        workspace = Path(self.temporary_directory.name) / "workspace"
        workspace.mkdir()
        database = self.data_directory / DATABASE_NAME
        connection = sqlite3.connect(database)
        connection.executescript(
            """
            CREATE TABLE sessions (
                creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL UNIQUE,
                workspace_root TEXT NOT NULL,
                workspace_dev INTEGER,
                workspace_inode INTEGER,
                workspace_uid INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            PRAGMA user_version = 2;
            """
        )
        connection.execute(
            "INSERT INTO sessions (id, workspace_root, created_at, updated_at) VALUES (?, ?, 1, 1)",
            ("session-1", str(workspace)),
        )
        connection.commit()
        connection.close()
        os.chmod(database, 0o600)

        store = SessionStore(self.data_directory)
        store.initialize()

        self.assertEqual(store.health(), {"state": "ready"})
        self.assertNotIn("title", store.read_session("session-1"))
        assert store.connection is not None
        self.assertIn(
            "title",
            {row["name"] for row in store.connection.execute("PRAGMA table_info(sessions)")},
        )
        store.close()

    def test_revision_five_migrates_to_multiple_historical_approvals(self) -> None:
        store = SessionStore(self.data_directory)
        store.initialize()
        assert store.connection is not None
        store.connection.execute("PRAGMA user_version = 5")
        store.connection.commit()
        store.close()

        migrated = SessionStore(self.data_directory)
        migrated.initialize()
        assert migrated.connection is not None
        indexes = {
            row[1]
            for row in migrated.connection.execute("PRAGMA index_list(approvals)")
        }

        self.assertEqual(
            migrated.connection.execute("PRAGMA user_version").fetchone()[0],
            SCHEMA_REVISION,
        )
        self.assertIn("one_pending_approval_per_item", indexes)
        migrated.close()

    def test_revision_seven_adds_progress_signature_with_verified_backup(self) -> None:
        store = SessionStore(self.data_directory)
        store.initialize()
        assert store.connection is not None
        store.connection.execute("ALTER TABLE steps DROP COLUMN progress_signature_json")
        store.connection.execute("PRAGMA user_version = 7")
        store.connection.commit()
        store.close()

        migrated = SessionStore(self.data_directory)
        migrated.initialize()
        assert migrated.connection is not None
        columns = {
            row["name"]
            for row in migrated.connection.execute("PRAGMA table_info(steps)")
        }
        manifests = list(self.data_directory.glob("*.rev7.*.bak.json"))

        self.assertEqual(migrated.health(), {"state": "ready"})
        self.assertEqual(
            migrated.connection.execute("PRAGMA user_version").fetchone()[0],
            SCHEMA_REVISION,
        )
        self.assertIn("progress_signature_json", columns)
        self.assertEqual(len(manifests), 1)
        migrated.close()

    def test_migration_failure_keeps_source_revision_and_enters_health_only(self) -> None:
        original = SessionStore(self.data_directory)
        original.initialize()
        assert original.connection is not None
        original.connection.execute("PRAGMA user_version = 1")
        original.connection.commit()
        original.close()

        with patch("eidos_runtime.db.storage._migrate_v1_to_v2", side_effect=StorageError("migration_failed")):
            failed = SessionStore(self.data_directory)
            failed.initialize()
        self.assertEqual(
            failed.health(), {"state": "health_only", "code": "migration_failed"}
        )
        connection = sqlite3.connect(self.data_directory / DATABASE_NAME)
        self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
        connection.close()

    def test_corrupt_database_enters_health_only(self) -> None:
        database = self.data_directory / DATABASE_NAME
        database.write_bytes(b"not sqlite")
        os.chmod(database, 0o600)
        store = SessionStore(self.data_directory)
        store.initialize()
        self.assertEqual(store.health()["state"], "health_only")
        self.assertIsNone(store.connection)

    def test_reserve_allocation_failure_starts_health_only_without_business_db(self) -> None:
        with patch("eidos_runtime.db.storage._prepare_reserve", side_effect=OSError("disk full")):
            store = SessionStore(self.data_directory)
            store.initialize()
        self.assertEqual(
            store.health(), {"state": "health_only", "code": "storage_io_error"}
        )
        self.assertFalse((self.data_directory / DATABASE_NAME).exists())

    def test_damaged_reserve_is_rejected_before_database_open(self) -> None:
        reserve = self.data_directory / RESERVE_NAME
        reserve.write_bytes(b"short")
        os.chmod(reserve, 0o600)
        store = SessionStore(self.data_directory)
        store.initialize()
        self.assertEqual(
            store.health(), {"state": "health_only", "code": "reserve_invalid"}
        )
        self.assertFalse((self.data_directory / DATABASE_NAME).exists())


if __name__ == "__main__":
    unittest.main()
