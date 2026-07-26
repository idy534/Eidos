from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.db.storage import SessionStore  # noqa: E402


class AsyncOperationJournalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="eidos-phase5c-async-"
        )
        self.data = Path(self.temporary.name) / "data"
        self.data.mkdir(mode=0o700)
        self.store = SessionStore(self.data)
        self.store.initialize()

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _accept(self):
        return self.store.accept_async_operation(
            request_id="client-request",
            operation_id="operation-1",
            scope="fixture",
            request={"value": 1},
        )

    def test_duplicate_async_operation_does_not_execute_twice(
        self,
    ) -> None:
        first, created = self._accept()
        duplicate, duplicate_created = self._accept()

        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(duplicate.id, first.id)
        self.assertEqual(duplicate.status, "accepted")

    def test_async_operation_result_is_replayable(self) -> None:
        operation, _ = self._accept()
        self.store.start_async_operation(operation.id)
        completed = self.store.complete_async_operation(
            operation.id, {"answer": 42}
        )
        replay, created = self._accept()

        self.assertEqual(completed.status, "completed")
        self.assertFalse(created)
        self.assertEqual(replay.result, {"answer": 42})

    def test_running_async_operation_returns_operation_in_progress(
        self,
    ) -> None:
        operation, _ = self._accept()
        self.store.start_async_operation(operation.id)

        duplicate, created = self._accept()

        self.assertFalse(created)
        self.assertEqual(duplicate.status, "running")

    def test_shutdown_cancels_async_operation(self) -> None:
        operation, _ = self._accept()
        self.store.start_async_operation(operation.id)

        canceled = self.store.cancel_active_async_operations()

        self.assertEqual(canceled[0].status, "canceled")

    def test_recovery_marks_async_operation_interrupted(self) -> None:
        operation, _ = self._accept()
        self.store.start_async_operation(operation.id)
        self.store.close()
        self.store = SessionStore(self.data)
        self.store.initialize()
        assert self.store.connection is not None

        row = self.store.connection.execute(
            "SELECT status, error_code FROM async_operations WHERE id = ?",
            (operation.id,),
        ).fetchone()

        self.assertEqual(
            tuple(row),
            ("interrupted", "ASYNC_OPERATION_INTERRUPTED"),
        )

    def test_async_operation_status_constraint_rejects_unknown_value(
        self,
    ) -> None:
        assert self.store.connection is not None
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.connection.execute(
                """
                INSERT INTO async_operations (
                    id, operation_id, scope, request_hash,
                    status, created_at
                ) VALUES ('bad', 'bad', 'bad', 'bad', 'unknown', 1)
                """
            )


if __name__ == "__main__":
    unittest.main()
