from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from eidos_runtime.db.schema import (
    SCHEMA_VERSION,
    V10_CONTEXT_SCHEMA_SQL,
    V10_REPOSITORY_SCHEMA_SQL,
    V12_BASE_SCHEMA_SQL,
)
from eidos_runtime.db.storage import DATABASE_NAME, SessionStore
from eidos_runtime.persistence.response_actions import ResponseActionRepository


class ResponseActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-response-actions-")
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        self.workspace = self.root / "workspace"
        self.data.mkdir(mode=0o700)
        self.workspace.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_v12_database_migrates_to_v13(self) -> None:
        database = self.data / DATABASE_NAME
        connection = sqlite3.connect(database)
        connection.executescript(
            V12_BASE_SCHEMA_SQL
            + V10_REPOSITORY_SCHEMA_SQL
            + V10_CONTEXT_SCHEMA_SQL
        )
        connection.execute("PRAGMA user_version = 12")
        connection.commit()
        connection.close()
        os.chmod(database, 0o600)

        store = SessionStore(self.data)
        store.initialize()
        self.assertEqual(store.health(), {"state": "ready"})
        assert store.connection is not None
        self.assertEqual(
            store.connection.execute("PRAGMA user_version").fetchone()[0],
            SCHEMA_VERSION,
        )
        tables = {
            str(row[0])
            for row in store.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertIn("response_feedback", tables)
        self.assertIn("run_revisions", tables)
        self.assertEqual(store.connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        store.close()

    def test_feedback_is_persisted_and_can_be_cleared(self) -> None:
        store, _run_id, assistant_item_id = self._completed_run_with_response()
        repository = ResponseActionRepository(store)

        self.assertEqual(
            repository.set_feedback(assistant_item_id, "up"),
            {"itemId": assistant_item_id, "feedback": "up"},
        )
        session_id = str(store.read_run(_run_id)["sessionId"])
        self.assertEqual(
            repository.state_for_session(session_id)["feedback"],
            [{"itemId": assistant_item_id, "value": "up"}],
        )

        repository.set_feedback(assistant_item_id, "down")
        self.assertEqual(
            repository.state_for_session(session_id)["feedback"],
            [{"itemId": assistant_item_id, "value": "down"}],
        )

        repository.set_feedback(assistant_item_id, None)
        self.assertEqual(repository.state_for_session(session_id)["feedback"], [])
        store.close()

    def test_revision_hides_source_run_from_future_model_context(self) -> None:
        store, source_run_id, _assistant_item_id = self._completed_run_with_response()
        repository = ResponseActionRepository(store)
        source = store.read_run(source_run_id)
        session_id = str(source["sessionId"])

        replacement, _replacement_user = store.enqueue_run(
            session_id,
            "edited question",
            model_id=str(source["modelId"]),
        )
        replacement_run_id = str(replacement["id"])
        repository.record_revision(
            run_id=replacement_run_id,
            source_run_id=source_run_id,
            revision_kind="edit",
        )

        state = repository.state_for_session(session_id)
        self.assertEqual(
            state["revisions"],
            [{
                "runId": replacement_run_id,
                "sourceRunId": source_run_id,
                "kind": "edit",
            }],
        )

        facts = store.context_projection_facts(replacement_run_id)
        self.assertEqual(
            [item.content for item in facts.items if item.kind == "user_message"],
            ["edited question"],
        )
        self.assertNotIn(source_run_id, {item.run_id for item in facts.items})
        store.close()

    def _completed_run_with_response(self) -> tuple[SessionStore, str, str]:
        store = SessionStore(self.data)
        store.initialize()
        self.assertEqual(store.health(), {"state": "ready"})
        session = store.create_session(str(self.workspace))
        run, _user = store.create_run(str(session["id"]), "original question")
        run_id = str(run["id"])
        assistant_item_id = "11111111-1111-4111-8111-111111111111"
        assert store.connection is not None
        with store.lock, store.connection:
            store.connection.execute(
                """
                INSERT INTO items (
                    id, session_id, run_id, ordinal, model_step_index,
                    kind, status, content, incomplete, created_at, completed_at
                ) VALUES (?, ?, ?, 2, 1, 'assistant_message', 'completed', ?, 0, 2, 2)
                """,
                (assistant_item_id, session["id"], run_id, "old answer"),
            )
            store.connection.execute(
                """
                UPDATE runs
                SET status = 'succeeded', completed_at = 2, updated_at = 2
                WHERE id = ?
                """,
                (run_id,),
            )
        return store, run_id, assistant_item_id


if __name__ == "__main__":
    unittest.main()
