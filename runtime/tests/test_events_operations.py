from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.events import IncompatibleEventError  # noqa: E402
from eidos_runtime.storage import (  # noqa: E402
    OperationConflictError,
    SessionStore,
)


class EventAndOperationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="eidos-events-")
        root = Path(self.temporary_directory.name)
        self.data = root / "data"
        self.workspace = root / "workspace"
        self.data.mkdir(mode=0o700)
        self.workspace.mkdir()
        self.store = SessionStore(self.data)
        self.store.initialize()

    def tearDown(self) -> None:
        self.store.close()
        self.temporary_directory.cleanup()

    def test_same_operation_replays_result_without_duplicate_fact_or_event(self) -> None:
        operation_id = "11111111-1111-4111-8111-111111111111"
        first = self.store.create_session(str(self.workspace), operation_id=operation_id)
        second = self.store.create_session(str(self.workspace), operation_id=operation_id)
        self.assertEqual(second, first)
        connection = self.store.connection
        assert connection is not None
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 1)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)

    def test_same_operation_with_different_request_conflicts(self) -> None:
        operation_id = "11111111-1111-4111-8111-111111111111"
        self.store.create_session(str(self.workspace), operation_id=operation_id)
        other = Path(self.temporary_directory.name) / "other"
        other.mkdir()
        with self.assertRaises(OperationConflictError):
            self.store.create_session(str(other), operation_id=operation_id)

    def test_cancel_operation_replay_does_not_duplicate_event(self) -> None:
        session = self.store.create_session(str(self.workspace))
        run, _ = self.store.enqueue_run(session["id"], "queued")
        operation_id = "22222222-2222-4222-8222-222222222222"
        first = self.store.cancel_run(run["id"], operation_id=operation_id)
        replay = self.store.cancel_run(run["id"], operation_id=operation_id)
        events = self.store.list_events(session["id"], after_event_id=0, limit=100)
        canceled = [
            event for event in events["items"]
            if event["eventType"] == "run.status_changed"
        ]
        self.assertEqual(first, replay)
        self.assertEqual(len(canceled), 1)

    def test_event_failure_rolls_back_fact_and_operation(self) -> None:
        operation_id = "11111111-1111-4111-8111-111111111111"
        with patch("eidos_runtime.storage.append_event", side_effect=ValueError("fixture")):
            with self.assertRaises(ValueError):
                self.store.create_session(str(self.workspace), operation_id=operation_id)
        connection = self.store.connection
        assert connection is not None
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM operations").fetchone()[0], 0)

    def test_snapshot_waterline_and_event_resume_do_not_miss_events(self) -> None:
        session = self.store.create_session(str(self.workspace))
        snapshot = self.store.read_session_snapshot(session["id"])
        self.assertEqual(snapshot["throughEventId"], 1)
        self.store.create_run(session["id"], "hello")
        resumed = self.store.list_events(
            session["id"], after_event_id=snapshot["throughEventId"]
        )
        self.assertEqual([event["eventType"] for event in resumed["items"]], ["run.created"])

    def test_session_title_is_persisted_once(self) -> None:
        session = self.store.create_session(str(self.workspace))

        first, _ = self.store.create_run(
            session["id"], "first", session_title="分析 Codex 架构"
        )
        self.store.fail_run(first["id"], "fixture")
        self.store.create_run(session["id"], "second", session_title="不应覆盖")
        unchanged = self.store.read_session(session["id"])

        assert unchanged is not None
        self.assertEqual(unchanged["title"], "分析 Codex 架构")
        events = self.store.list_events(session["id"], after_event_id=0)
        self.assertEqual(
            sum(
                event["eventType"] == "session.title_updated"
                for event in events["items"]
            ),
            1,
        )

    def test_unknown_event_type_is_ignored_but_unknown_version_is_incompatible(self) -> None:
        session = self.store.create_session(str(self.workspace))
        connection = self.store.connection
        assert connection is not None
        connection.execute(
            """
            INSERT INTO events (
                event_contract_version, event_type, occurred_at,
                session_id, payload_json
            ) VALUES (1, 'future.ignorable', 2, ?, '{}')
            """,
            (session["id"],),
        )
        connection.commit()
        listed = self.store.list_events(session["id"], after_event_id=1)
        self.assertEqual(listed["items"], [])
        connection.execute(
            """
            INSERT INTO events (
                event_contract_version, event_type, occurred_at,
                session_id, payload_json
            ) VALUES (2, 'run.created', 3, ?, '{}')
            """,
            (session["id"],),
        )
        connection.commit()
        with self.assertRaises(IncompatibleEventError):
            self.store.list_events(session["id"], after_event_id=2)

    def test_session_cursor_keeps_original_high_water_when_new_rows_arrive(self) -> None:
        workspaces = []
        for index in range(4):
            workspace = Path(self.temporary_directory.name) / f"workspace-{index}"
            workspace.mkdir()
            workspaces.append(workspace)
            self.store.create_session(str(workspace))
        first = self.store.list_sessions(limit=2)
        newest_ids = [session["id"] for session in first["items"]]
        late = Path(self.temporary_directory.name) / "workspace-late"
        late.mkdir()
        late_session = self.store.create_session(str(late))
        second = self.store.list_sessions(limit=2, cursor=first["nextCursor"])
        second_ids = [session["id"] for session in second["items"]]
        self.assertFalse(set(newest_ids) & set(second_ids))
        self.assertNotIn(late_session["id"], second_ids)

    def test_persistent_fifo_claims_one_run_at_a_time_and_survives_restart(self) -> None:
        session = self.store.create_session(str(self.workspace))
        first, _ = self.store.enqueue_run(session["id"], "first")
        second, _ = self.store.enqueue_run(session["id"], "second")
        claimed = self.store.claim_next_run()
        self.assertEqual(claimed["id"], first["id"])
        self.assertEqual(self.store.read_run(second["id"])["status"], "queued")
        self.store.fail_run(first["id"], "fixture")
        self.store.close()
        self.store = SessionStore(self.data)
        self.store.initialize()
        claimed_after_restart = self.store.claim_next_run()
        self.assertEqual(claimed_after_restart["id"], second["id"])

    def test_canceling_queued_run_does_not_change_remaining_fifo_order(self) -> None:
        session = self.store.create_session(str(self.workspace))
        first, _ = self.store.enqueue_run(session["id"], "first")
        second, _ = self.store.enqueue_run(session["id"], "second")
        third, _ = self.store.enqueue_run(session["id"], "third")
        self.store.cancel_run(second["id"])
        self.assertEqual(self.store.claim_next_run()["id"], first["id"])
        self.store.fail_run(first["id"], "fixture")
        self.assertEqual(self.store.claim_next_run()["id"], third["id"])


if __name__ == "__main__":
    unittest.main()
