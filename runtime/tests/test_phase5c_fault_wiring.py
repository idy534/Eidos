from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import threading
import unittest


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.protocol.server import RuntimeServer  # noqa: E402
from eidos_runtime.runtime.fault_injection import (  # noqa: E402
    injected_faults,
)
from eidos_runtime.sandbox.workspace_index import (  # noqa: E402
    WorkspaceIndexIncomplete,
)
from eidos_runtime.tools.workspace import ToolExecutor  # noqa: E402
from eidos_runtime.tools.workspace import WorkspacePathError  # noqa: E402
from tests.fault_injection import (  # noqa: E402
    FAULT_POINTS,
    assert_runtime_converged,
)


FAULT_LOCATIONS = {
    "model_stream_block": "model/pydantic_ai_client.py",
    "model_cancel_delay": "model/pydantic_ai_client.py",
    "tool_block": "runtime/tool_execution.py",
    "tool_late_result": "runtime/tool_execution.py",
    "shell_ignore_sigterm": "sandbox/shell.py",
    "shell_modify_then_fail": "sandbox/shell.py",
    "mcp_ignore_protocol_cancel": "extensions/mcp.py",
    "mcp_thread_stuck": "extensions/mcp.py",
    "workspace_manifest_timeout": "sandbox/workspace_index.py",
    "sqlite_append_event_failure": "db/events.py",
    "sqlite_commit_failure": "db/database.py",
    "finalization_model_failure": "runtime/finalizer.py",
    "jsonrpc_output_disconnect": "protocol/server.py",
    "cancel_claim_race": "runtime/supervisor.py",
    "configure_worker_race": "runtime/supervisor.py",
    "cancel_approval_race": "runtime/approval.py",
    "cancel_finalization_race": "runtime/finalizer.py",
    "shutdown_tool_completion_race": "runtime/supervisor.py",
}


class _FailAt:
    def __init__(self, point: str, error: BaseException) -> None:
        self.point = point
        self.error = error
        self.hits = 0

    def hit(self, point: str) -> None:
        if point == self.point:
            self.hits += 1
            raise self.error


class FaultWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="eidos-phase5c-faults-"
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

    def test_all_fault_points_are_wired_to_production_paths(self) -> None:
        self.assertEqual(set(FAULT_LOCATIONS), set(FAULT_POINTS))
        for point, relative in FAULT_LOCATIONS.items():
            source = (
                RUNTIME_ROOT / "eidos_runtime" / relative
            ).read_text(encoding="utf-8")
            self.assertIn(f'hit_fault("{point}")', source)

    def test_sqlite_append_event_failure_runs_real_transaction(
        self,
    ) -> None:
        injector = _FailAt(
            "sqlite_append_event_failure",
            RuntimeError("fixture append failure"),
        )
        with (
            injected_faults(injector),
            self.assertRaises(RuntimeError),
        ):
            self.store.create_session(str(self.workspace))

        assert self.store.connection is not None
        self.assertEqual(
            self.store.connection.execute(
                "SELECT COUNT(*) FROM sessions"
            ).fetchone()[0],
            0,
        )
        assert_runtime_converged(self.store)

    def test_sqlite_commit_failure_runs_real_transaction(self) -> None:
        injector = _FailAt(
            "sqlite_commit_failure",
            RuntimeError("fixture commit failure"),
        )
        with (
            injected_faults(injector),
            self.assertRaises(RuntimeError),
        ):
            with self.store._database.transaction() as connection:
                connection.execute("SELECT 1")

        self.assertEqual(injector.hits, 1)
        assert_runtime_converged(self.store)

    def test_workspace_manifest_timeout_runs_real_index(self) -> None:
        executor = ToolExecutor(self.workspace)
        injector = _FailAt(
            "workspace_manifest_timeout",
            WorkspaceIndexIncomplete(),
        )
        try:
            with (
                injected_faults(injector),
                self.assertRaisesRegex(
                    WorkspacePathError, "WORKSPACE_INDEX_INCOMPLETE"
                ),
            ):
                executor.refresh_workspace_index(threading.Event())
        finally:
            executor.close()
        assert_runtime_converged(self.store)

    def test_jsonrpc_output_disconnect_runs_real_server_send(
        self,
    ) -> None:
        import io

        server = RuntimeServer(io.StringIO(), self.data)
        injector = _FailAt(
            "jsonrpc_output_disconnect",
            BrokenPipeError("fixture disconnect"),
        )
        with (
            injected_faults(injector),
            self.assertRaises(BrokenPipeError),
        ):
            server.send({"jsonrpc": "2.0", "id": "client", "result": {}})


if __name__ == "__main__":
    unittest.main()
