from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.db.database import CommittedMutation  # noqa: E402
from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.extensions.mcp import (  # noqa: E402
    McpConnection,
    McpShutdownTimeout,
)
from eidos_runtime.model.pydantic_ai_client import (  # noqa: E402
    ModelClientFactory,
    ModelClientLease,
)
from eidos_runtime.protocol.server import RuntimeServer  # noqa: E402
from eidos_runtime.runtime.supervisor import (  # noqa: E402
    RunSupervisor,
    RuntimeControlState,
    RuntimeShutdownTimeout,
)


class _ExitOnCancelEngine:
    entered = threading.Event()

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def run(self, _run_id: str, cancel: threading.Event) -> None:
        self.entered.set()
        while not cancel.wait(0.01):
            pass
        from eidos_runtime.runtime.contracts import RuntimeCancelled

        raise RuntimeCancelled


class SupervisorRaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-phase5b0-race-")
        root = Path(self.temporary.name)
        data = root / "data"
        workspace = root / "workspace"
        data.mkdir(mode=0o700)
        workspace.mkdir()
        self.store = SessionStore(data)
        self.store.initialize()
        self.session = self.store.create_session(str(workspace))
        _ExitOnCancelEngine.entered = threading.Event()

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _supervisor(self, model_for=None) -> RunSupervisor:
        return RunSupervisor(
            self.store,
            model_for or (lambda _model_id: ModelClientLease(object())),
            lambda _message: None,
            lambda value: value,
            lambda: True,
            lambda: False,
            lambda: None,
            engine_factory=_ExitOnCancelEngine,
        )

    def test_cancel_and_claim_are_mutually_exclusive(self) -> None:
        run, _ = self.store.enqueue_run(self.session["id"], "cancel")
        supervisor = self._supervisor()
        cancel_inside_store = threading.Barrier(2)
        release_cancel = threading.Barrier(2)
        original = self.store.cancel_run

        def blocked_cancel(run_id: str, *, operation_id=None):
            cancel_inside_store.wait()
            release_cancel.wait()
            return original(run_id, operation_id=operation_id)

        claimed: list[object] = []
        with patch.object(self.store, "cancel_run", blocked_cancel):
            cancel_thread = threading.Thread(target=lambda: supervisor.cancel_run(run["id"]))
            cancel_thread.start()
            cancel_inside_store.wait()
            claim_thread = threading.Thread(
                target=lambda: claimed.append(supervisor.prepare_next())
            )
            claim_thread.start()
            time.sleep(0.03)
            self.assertTrue(claim_thread.is_alive())
            release_cancel.wait()
            cancel_thread.join(1)
            claim_thread.join(1)

        self.assertEqual(claimed, [None])
        self.assertEqual(self.store.read_run(run["id"])["status"], "canceled")

    def test_cancel_queued_run_cannot_start_worker_after_terminal_commit(self) -> None:
        run, _ = self.store.enqueue_run(self.session["id"], "cancel")
        supervisor = self._supervisor()

        canceled = supervisor.cancel_run(run["id"])
        supervisor.schedule_next()

        self.assertEqual(canceled["status"], "canceled")
        self.assertFalse(_ExitOnCancelEngine.entered.wait(0.05))
        self.assertNotIn(run["id"], supervisor._handles)

    def test_claim_ignores_cancel_requested_run(self) -> None:
        run, _ = self.store.enqueue_run(self.session["id"], "cancel")
        self.store.request_cancel_committed(run["id"])

        self.assertIsNone(self.store.claim_next_run_committed())
        self.assertEqual(self.store.read_run(run["id"])["status"], "queued")

    def test_cancel_success_means_no_handle_can_appear_after_return(self) -> None:
        run, _ = self.store.enqueue_run(self.session["id"], "cancel")
        supervisor = self._supervisor()

        supervisor.cancel_run(run["id"])
        threads = [
            threading.Thread(target=supervisor.schedule_next) for _ in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(1)

        self.assertNotIn(run["id"], supervisor._handles)

    def test_configure_and_prepare_next_are_mutually_exclusive(self) -> None:
        self.store.enqueue_run(self.session["id"], "queued")
        supervisor = self._supervisor()
        self.assertTrue(supervisor.begin_reconfiguration())
        started: list[object] = []

        thread = threading.Thread(target=lambda: started.append(supervisor.prepare_next()))
        thread.start()
        thread.join(1)

        self.assertEqual(started, [None])
        self.assertEqual(supervisor.control_state, RuntimeControlState.RECONFIGURING)
        supervisor.end_reconfiguration()
        start = supervisor.prepare_next()
        self.assertIsNotNone(start)
        supervisor.abort(start)
        supervisor.wait(1)

    def test_worker_cannot_acquire_lease_during_factory_swap(self) -> None:
        self.store.enqueue_run(self.session["id"], "queued")
        acquired = threading.Event()
        supervisor = self._supervisor(
            lambda _model_id: (acquired.set(), ModelClientLease(object()))[1]
        )

        self.assertTrue(supervisor.begin_reconfiguration())
        supervisor.schedule_next()
        self.assertFalse(acquired.wait(0.05))
        supervisor.end_reconfiguration()
        supervisor.schedule_next()
        self.assertTrue(acquired.wait(1))
        supervisor.shutdown()


class ConfigureFailureTests(unittest.TestCase):
    def _server(self, root: Path) -> RuntimeServer:
        data = root / "data"
        workspace = root / "workspace"
        data.mkdir(mode=0o700)
        workspace.mkdir()
        server = RuntimeServer(io.StringIO(), data)
        server.store.initialize()
        server.model_config.initialize()
        server.initialized = True
        return server

    def test_configure_failure_preserves_previous_factory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-phase5b0-config-") as temporary:
            server = self._server(Path(temporary))
            server.model_config.save_api_key("sk-existing-key-for-tests")
            previous = ModelClientFactory("sk-existing-key-for-tests")
            server.model_factory = previous

            with patch(
                "eidos_runtime.protocol.server.ModelClientFactory",
                side_effect=OSError("factory failed"),
            ):
                server.configure_model(
                    "client-config", {"apiKey": "sk-replacement-key-for-tests"}
                )

            self.assertIs(server.model_factory, previous)
            self.assertEqual(
                server.model_config.api_key(), "sk-existing-key-for-tests"
            )
            previous.close()
            server.model_factory = None
            server.close()

    def test_configure_releases_gate_after_failure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-phase5b0-config-") as temporary:
            server = self._server(Path(temporary))
            with patch(
                "eidos_runtime.protocol.server.ModelClientFactory",
                side_effect=OSError("factory failed"),
            ):
                server.configure_model(
                    "client-config", {"apiKey": "sk-replacement-key-for-tests"}
                )

            self.assertEqual(
                server.supervisor.control_state, RuntimeControlState.RUNNING
            )
            server.close()

    def test_model_factory_in_use_is_mapped_to_business_error(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-phase5b0-config-") as temporary:
            server = self._server(Path(temporary))
            server.model_factory = ModelClientFactory("sk-existing-key-for-tests")
            lease = server.model_factory.acquire("deepseek-v4-flash")

            server.configure_model(
                "client-config", {"apiKey": "sk-replacement-key-for-tests"}
            )

            message = json.loads(server.output.getvalue().splitlines()[-1])
            self.assertEqual(
                message["error"]["data"]["code"], "MODEL_CLIENT_IN_USE"
            )
            lease.close()
            server.close()


class CommittedMutationOrderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-phase5b0-events-")
        root = Path(self.temporary.name)
        data = root / "data"
        workspace = root / "workspace"
        data.mkdir(mode=0o700)
        workspace.mkdir()
        self.store = SessionStore(data)
        self.store.initialize()
        self.session = self.store.create_session(str(workspace))

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_approval_resolution_events_are_in_event_id_order(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "approve")
        self.store.increment_model_step(run["id"])
        item = self.store.create_tool_item(
            run["id"], 1, 0, "call", "write_file", "{}"
        )
        self.store.begin_approval(item["id"], "diff", None)

        mutation = self.store.resolve_approval_committed(
            item["id"], "approve", None
        )

        ids = tuple(event["eventId"] for event in mutation.events)
        self.assertEqual(ids, tuple(sorted(ids)))

    def test_committed_mutation_does_not_validate_order_after_commit(
        self,
    ) -> None:
        mutation = CommittedMutation(
            {},
            (
                {"eventId": 2},
                {"eventId": 1},
                {"eventId": True},
            ),
        )

        self.assertEqual(mutation.event_ids, (2, 1))

    def test_event_projection_preserves_commit_order(self) -> None:
        from eidos_runtime.runtime.events import RuntimeEvents

        delivered: list[int] = []
        events = RuntimeEvents(
            lambda message: delivered.append(int(message["params"]["eventId"]))
        )
        mutation = CommittedMutation(
            {},
            (
                {
                    "eventId": 1,
                    "eventType": "unknown",
                    "payload": {},
                },
                {
                    "eventId": 2,
                    "eventType": "unknown",
                    "payload": {},
                },
            ),
        )
        with patch.object(
            events._projector,
            "project",
            side_effect=lambda event, **_kwargs: [
                {
                    "jsonrpc": "2.0",
                    "method": "test",
                    "params": {"eventId": event["eventId"]},
                }
            ],
        ):
            events.publish(mutation)
        self.assertEqual(delivered, [1, 2])


class McpCloseContractTests(unittest.TestCase):
    def _connection(self) -> McpConnection:
        connection = object.__new__(McpConnection)
        connection.closed = threading.Event()
        connection.commands = __import__("queue").Queue()
        connection.runtime_root = Path("/tmp/eidos-mcp-test-do-not-create")
        return connection

    def test_mcp_close_success_means_thread_exited(self) -> None:
        connection = self._connection()
        release = threading.Event()
        connection.thread = threading.Thread(target=lambda: release.wait(0.05))
        connection.thread.start()
        connection._terminate_process_group = lambda: True

        self.assertTrue(connection.close())
        self.assertFalse(connection.thread.is_alive())

    def test_mcp_close_success_means_process_group_exited(self) -> None:
        connection = self._connection()
        connection.thread = threading.Thread(target=lambda: None)
        connection.thread.start()
        connection.thread.join()
        connection._terminate_process_group = lambda: False

        with self.assertRaises(McpShutdownTimeout):
            connection.close()

    def test_mcp_shutdown_timeout_is_visible(self) -> None:
        connection = self._connection()
        release = threading.Event()
        connection.thread = threading.Thread(target=release.wait)
        connection.thread.start()
        connection._terminate_process_group = lambda: True
        try:
            with patch.object(connection.thread, "join", return_value=None):
                with self.assertRaises(McpShutdownTimeout):
                    connection.close()
        finally:
            release.set()
            connection.thread.join(1)

    def test_runtime_quiescence_detects_live_mcp_thread(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-phase5b0-close-") as temporary:
            root = Path(temporary)
            data = root / "data"
            workspace = root / "workspace"
            data.mkdir(mode=0o700)
            workspace.mkdir()
            store = SessionStore(data)
            store.initialize()
            supervisor = RunSupervisor(
                store,
                lambda _model_id: ModelClientLease(object()),
                lambda _message: None,
                lambda value: value,
                lambda: True,
                lambda: False,
                lambda: None,
                shutdown_timeout=0.01,
            )
            release = threading.Event()
            thread = threading.Thread(
                target=release.wait, name="eidos-mcp-stuck", daemon=False
            )
            thread.start()
            try:
                with self.assertRaises(RuntimeShutdownTimeout):
                    supervisor.shutdown()
            finally:
                release.set()
                thread.join(1)
                store.close()

    def test_close_does_not_report_closed_after_shutdown_timeout(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-phase5b0-close-") as temporary:
            root = Path(temporary)
            data = root / "data"
            workspace = root / "workspace"
            data.mkdir(mode=0o700)
            workspace.mkdir()
            server = RuntimeServer(io.StringIO(), data, object())  # type: ignore[arg-type]
            server.store.initialize()
            server.model_config.initialize()
            server.initialized = True
            with patch.object(
                server.supervisor,
                "shutdown",
                side_effect=RuntimeShutdownTimeout("timeout"),
            ):
                with self.assertRaises(RuntimeShutdownTimeout):
                    server.close()
            self.assertNotEqual(server.supervisor.lifecycle.value, "closed")
            server.store.close()


if __name__ == "__main__":
    unittest.main()
