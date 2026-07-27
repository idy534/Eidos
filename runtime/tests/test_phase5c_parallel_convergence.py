from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.db.invariants import verify_runtime_invariants  # noqa: E402
from eidos_runtime.db.errors import StorageError  # noqa: E402
from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.model.client import (  # noqa: E402
    ModelResponse,
    ModelToolCall,
    ScriptedModel,
)
from eidos_runtime.runtime.contracts import RuntimeCancelled  # noqa: E402
from eidos_runtime.runtime.engine import RuntimeEngine  # noqa: E402
from eidos_runtime.runtime.tool_runtime import (  # noqa: E402
    ReadOnlyToolHandler,
)


class ParallelToolConvergenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="eidos-phase5c-parallel-"
        )
        root = Path(self.temporary.name)
        data = root / "data"
        self.workspace = root / "workspace"
        data.mkdir(mode=0o700)
        self.workspace.mkdir()
        self.store = SessionStore(data)
        self.store.initialize()
        self.session = self.store.create_session(str(self.workspace))

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_parallel_tool_db_failure_is_not_swallowed(self) -> None:
        run, _ = self.store.create_run(
            self.session["id"], "parallel infrastructure"
        )
        model = ScriptedModel([
            ModelResponse(tool_calls=(
                ModelToolCall("bad", "list_files", {}),
                ModelToolCall("sibling", "list_files", {}),
            )),
            ModelResponse(text="must not be sampled"),
        ])
        def injected(handler, run_id, item, call, cancel):
            if call.provider_call_id == "bad":
                raise StorageError("fixture database failure")
            while not cancel.is_set():
                cancel.wait(0.01)
            raise RuntimeCancelled

        with patch.object(ReadOnlyToolHandler, "execute", injected):
            RuntimeEngine(self.store, model, lambda _message: None).run(
                run["id"], threading.Event()
            )

        self.assertEqual(
            self.store.read_run(run["id"])["errorCode"],
            "TOOL_INFRASTRUCTURE_FAILURE",
        )
        self.assertEqual(len(model.contexts), 1)
        assert self.store.connection is not None
        running = self.store.connection.execute(
            """
            SELECT COUNT(*) FROM tool_calls
            JOIN items ON items.id = tool_calls.item_id
            WHERE items.run_id = ? AND tool_calls.status = 'running'
            """,
            (run["id"],),
        ).fetchone()[0]
        self.assertEqual(running, 0)
        verify_runtime_invariants(self.store.connection)


if __name__ == "__main__":
    unittest.main()
