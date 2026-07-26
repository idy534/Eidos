from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.db.database import CommittedMutation  # noqa: E402
from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.runtime.events import RuntimeEvents  # noqa: E402


class EventOutboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="eidos-phase5c-outbox-"
        )
        root = Path(self.temporary.name)
        self.data = root / "data"
        self.workspace = root / "workspace"
        self.data.mkdir(mode=0o700)
        self.workspace.mkdir()
        self.store = SessionStore(self.data)
        self.store.initialize()

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _title_mutation(
        self, suffix: str
    ) -> CommittedMutation[dict[str, object]]:
        workspace = self.workspace / suffix
        workspace.mkdir()
        session = self.store.create_session(str(workspace))
        self.store.begin_title_generation_committed(session["id"])
        return self.store.finish_title_generation_committed(
            session["id"], f"title-{suffix}"
        )

    def test_business_fact_and_outbox_commit_atomically(self) -> None:
        mutation = self._title_mutation("atomic")
        assert self.store.connection is not None

        rows = self.store.connection.execute(
            """
            SELECT event_id, status FROM event_outbox
            WHERE event_id IN (
                SELECT id FROM events WHERE session_id = ?
            )
            ORDER BY event_id
            """,
            (mutation.value["id"],),
        ).fetchall()

        self.assertTrue(rows)
        self.assertTrue(all(row["status"] == "pending" for row in rows))

    def test_event_ids_are_read_in_database_order(self) -> None:
        first = self._title_mutation("first")
        second = self._title_mutation("second")
        delivered: list[str] = []
        events = RuntimeEvents(
            lambda message: delivered.append(
                str(message["params"]["title"])
            ),
            store=self.store,
        )
        reversed_mutation = CommittedMutation(
            second.value,
            tuple(reversed(first.events + second.events)),
        )

        events.publish(reversed_mutation)

        self.assertEqual(delivered, ["title-first", "title-second"])

    def test_output_disconnect_keeps_events_pending(self) -> None:
        mutation = self._title_mutation("disconnect")
        events = RuntimeEvents(
            lambda _message: (_ for _ in ()).throw(
                BrokenPipeError("closed")
            ),
            store=self.store,
        )

        result = events.publish(mutation)

        self.assertTrue(result.failures)
        self.assertEqual(self.store.pending_outbox_count(), 1)

    def test_later_event_cannot_pass_earlier_pending_event(self) -> None:
        first = self._title_mutation("blocked")
        second = self._title_mutation("later")
        blocked = True
        delivered: list[str] = []

        def notify(message):
            if blocked:
                raise BrokenPipeError("closed")
            delivered.append(str(message["params"]["title"]))

        events = RuntimeEvents(notify, store=self.store)
        events.publish(first)
        blocked = False
        events.publish(second)

        self.assertEqual(delivered, ["title-blocked", "title-later"])
        self.assertEqual(self.store.pending_outbox_count(), 0)

    def test_transaction_rollback_creates_no_outbox_record(self) -> None:
        assert self.store.connection is not None
        before = self.store.connection.execute(
            "SELECT COUNT(*) FROM event_outbox"
        ).fetchone()[0]
        self.store.connection.execute(
            """
            CREATE TEMP TRIGGER reject_outbox
            BEFORE INSERT ON event_outbox
            BEGIN SELECT RAISE(ABORT, 'fixture outbox failure'); END
            """
        )
        workspace = self.workspace / "rollback"
        workspace.mkdir()

        with self.assertRaises(sqlite3.IntegrityError):
            self.store.create_session(str(workspace))

        after = self.store.connection.execute(
            "SELECT COUNT(*) FROM event_outbox"
        ).fetchone()[0]
        self.assertEqual(after, before)

    def test_recovery_preserves_pending_events(self) -> None:
        self._title_mutation("recovery")
        pending = self.store.pending_outbox_count()
        self.store.close()
        self.store = SessionStore(self.data)
        self.store.initialize()

        self.assertEqual(self.store.pending_outbox_count(), pending)


if __name__ == "__main__":
    unittest.main()
