from __future__ import annotations

import json
import io
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.db.invariants import (  # noqa: E402
    RuntimeInvariantError,
    verify_runtime_invariants,
)
from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.model.pydantic_ai_client import (  # noqa: E402
    ModelClientFactory,
    ModelClientInUseError,
    ModelClientLease,
)
from eidos_runtime.protocol.server import RuntimeServer  # noqa: E402
from eidos_runtime.runtime.supervisor import (  # noqa: E402
    RunCancelTimeout,
    RunReconciliationRequired,
    RunSupervisor,
    RunWorkerState,
    RuntimeShutdownTimeout,
)
from eidos_runtime.runtime.state_machine import RuntimeLifecycle  # noqa: E402


class RuntimeStateConsistencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-phase5a-state-")
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

    def _reject(self, run_id: str, index: int) -> dict[str, object]:
        item = self.store.create_tool_item(
            run_id,
            index,
            0,
            f"call-{index}",
            "write_file",
            json.dumps({"path": "a.txt", "content": "x"}),
        )
        self.store.begin_approval(item["id"], "diff", None)
        self.store.resolve_approval(item["id"], "reject", None)
        self.store.complete_tool_item(
            item["id"],
            json.dumps({"outcome": "declined", "code": "user_rejected"}),
            item_status="declined",
        )
        return item

    def test_rejections_keep_agent_and_segment_running(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "change")
        self.store.increment_model_step(run["id"])
        self.store.complete_current_step(run["id"], "completed")
        self._reject(run["id"], 1)
        self._reject(run["id"], 2)

        connection = self.store.connection
        assert connection is not None
        self.assertEqual(self.store.read_run(run["id"])["status"], "running")
        self.assertEqual(
            connection.execute(
                "SELECT status FROM execution_segments WHERE run_id = ?",
                (run["id"],),
            ).fetchone()["status"],
            "running",
        )

    def test_only_one_running_segment_per_run(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "segment")
        self.store.increment_model_step(run["id"])
        connection = self.store.connection
        assert connection is not None
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO execution_segments
                    (id, run_id, ordinal, status, created_at, started_at)
                VALUES ('duplicate-segment', ?, 2, 'running', 1, 1)
                """,
                (run["id"],),
            )

    def test_only_one_running_step_per_run(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "step")
        self.store.increment_model_step(run["id"])
        connection = self.store.connection
        assert connection is not None
        segment_id = connection.execute(
            "SELECT id FROM execution_segments WHERE run_id = ?", (run["id"],)
        ).fetchone()["id"]
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO steps
                    (id, run_id, segment_id, ordinal, status, created_at)
                VALUES ('duplicate-step', ?, ?, 2, 'running', 1)
                """,
                (run["id"], segment_id),
            )

    def test_only_one_running_attempt_per_step(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "attempt")
        self.store.increment_model_step(run["id"])
        connection = self.store.connection
        assert connection is not None
        step_id = connection.execute(
            "SELECT id FROM steps WHERE run_id = ?", (run["id"],)
        ).fetchone()["id"]
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO model_attempts
                    (id, step_id, ordinal, status, started_at)
                VALUES ('duplicate-attempt', ?, 2, 'running', 1)
                """,
                (step_id,),
            )

    def test_terminal_run_has_no_active_children(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "cancel")
        self.store.increment_model_step(run["id"])
        self.store.cancel_run(run["id"])
        connection = self.store.connection
        assert connection is not None

        verify_runtime_invariants(connection)

    def test_waiting_approval_has_exactly_one_pending_approval(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "approve")
        self.store.increment_model_step(run["id"])
        item = self.store.create_tool_item(
            run["id"], 1, 0, "call", "write_file", "{}"
        )
        self.store.begin_approval(item["id"], "diff", None)
        connection = self.store.connection
        assert connection is not None

        verify_runtime_invariants(connection)
        connection.execute(
            "UPDATE approvals SET status = 'invalidated' WHERE run_id = ?",
            (run["id"],),
        )
        with self.assertRaises(RuntimeInvariantError):
            verify_runtime_invariants(connection)


class RuntimeRecoveryInvariantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-phase5a-recovery-")
        root = Path(self.temporary.name)
        self.data = root / "data"
        workspace = root / "workspace"
        self.data.mkdir(mode=0o700)
        workspace.mkdir()
        self.store = SessionStore(self.data)
        self.store.initialize()
        self.session = self.store.create_session(str(workspace))

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _restart(self) -> None:
        self.store.close()
        self.store = SessionStore(self.data)
        self.store.initialize()

    def test_recovery_interrupts_running_segment(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "running")
        self.store.increment_model_step(run["id"])
        self.store.complete_current_step(run["id"], "completed")

        self._restart()

        assert self.store.connection is not None
        self.assertEqual(self.store.read_run(run["id"])["status"], "interrupted")
        self.assertEqual(
            self.store.connection.execute(
                "SELECT status FROM execution_segments WHERE run_id = ?",
                (run["id"],),
            ).fetchone()["status"],
            "failed",
        )

    def test_recovery_handles_cancel_requested_run(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "cancel")
        connection = self.store.connection
        assert connection is not None
        connection.execute(
            "UPDATE runs SET cancel_requested_at = 1 WHERE id = ?",
            (run["id"],),
        )
        connection.commit()

        self._restart()

        recovered = self.store.read_run(run["id"])
        self.assertEqual(recovered["status"], "canceled")
        self.assertIsNotNone(recovered["cancelCompletedAt"])

    def test_recovery_does_not_replay_uncertain_side_effect(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "write")
        self.store.increment_model_step(run["id"])
        item = self.store.create_tool_item(
            run["id"], 1, 0, "call", "write_file", "{}"
        )
        self.store.begin_approval(item["id"], "diff", None)
        self.store.resolve_approval(item["id"], "approve", None)
        self.store.begin_durable_intent(item["id"], preconditions={})
        connection = self.store.connection
        assert connection is not None
        connection.execute(
            "UPDATE runs SET cancel_requested_at = 1 WHERE id = ?",
            (run["id"],),
        )
        connection.commit()

        self._restart()

        recovered = self.store.read_run(run["id"])
        self.assertEqual(recovered["status"], "interrupted")
        self.assertTrue(recovered["sideEffectsMayExist"])
        assert self.store.connection is not None
        self.assertEqual(
            self.store.connection.execute(
                "SELECT status FROM durable_intents WHERE run_id = ?",
                (run["id"],),
            ).fetchone()["status"],
            "interrupted",
        )

    def test_recovery_finishes_with_valid_invariants(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "crash")
        self.store.increment_model_step(run["id"])

        self._restart()

        assert self.store.connection is not None
        verify_runtime_invariants(self.store.connection)


class _BlockingEngine:
    entered = threading.Event()
    release = threading.Event()

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def run(self, _run_id: str, _cancel: threading.Event) -> None:
        self.entered.set()
        self.release.wait(2)


class _ApprovalWaitingEngine:
    entered = threading.Event()

    def __init__(self, _store, _model, _notify, request_approval, *_args, **_kwargs):
        self.request_approval = request_approval

    def run(self, run_id: str, cancel: threading.Event) -> None:
        self.entered.set()
        self.request_approval({"runId": run_id}, cancel)


class _CancelAwareEngine:
    entered = threading.Event()
    cancel_seen = threading.Event()
    allow_exit = threading.Event()

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def run(self, _run_id: str, cancel: threading.Event) -> None:
        self.entered.set()
        while not cancel.wait(0.005):
            pass
        self.cancel_seen.set()
        self.allow_exit.wait(2)
        raise __import__(
            "eidos_runtime.runtime.contracts", fromlist=["RuntimeCancelled"]
        ).RuntimeCancelled()


class _StuckEngine:
    entered = threading.Event()
    release = threading.Event()

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def run(self, _run_id: str, cancel: threading.Event) -> None:
        self.entered.set()
        self.release.wait(2)
        if cancel.is_set():
            raise __import__(
                "eidos_runtime.runtime.contracts", fromlist=["RuntimeCancelled"]
            ).RuntimeCancelled()


class RunHandleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-phase5a-handle-")
        root = Path(self.temporary.name)
        data = root / "data"
        workspace = root / "workspace"
        data.mkdir(mode=0o700)
        workspace.mkdir()
        self.store = SessionStore(data)
        self.store.initialize()
        self.session = self.store.create_session(str(workspace))
        _BlockingEngine.entered = threading.Event()
        _BlockingEngine.release = threading.Event()
        _ApprovalWaitingEngine.entered = threading.Event()

    def tearDown(self) -> None:
        _BlockingEngine.release.set()
        self.store.close()
        self.temporary.cleanup()

    def _supervisor(self, engine_factory, *, cleanup=None) -> RunSupervisor:
        return RunSupervisor(
            self.store,
            lambda _model_id: ModelClientLease(object()),
            lambda _message: None,
            lambda value: value,
            lambda: True,
            lambda: False,
            lambda: None,
            cleanup=cleanup,
            engine_factory=engine_factory,
            cancel_timeout=0.05,
            shutdown_timeout=0.1,
        )

    def test_same_run_cannot_create_two_handles(self) -> None:
        run, _ = self.store.enqueue_run(self.session["id"], "block")
        supervisor = self._supervisor(_BlockingEngine)

        start = supervisor.prepare_next()
        self.assertIsNotNone(start)
        self.assertIsNone(supervisor.prepare_next())
        self.assertEqual(tuple(supervisor._handles), (run["id"],))

        supervisor.release(start)
        self.assertTrue(_BlockingEngine.entered.wait(1))
        _BlockingEngine.release.set()
        supervisor.wait(1)

    def test_waiting_approval_worker_remains_active(self) -> None:
        run, _ = self.store.enqueue_run(self.session["id"], "approve")
        supervisor = self._supervisor(_ApprovalWaitingEngine)
        supervisor.schedule_next()
        self.assertTrue(_ApprovalWaitingEngine.entered.wait(1))
        self.assertTrue(_wait_until(
            lambda: supervisor.handle_state(run["id"]) is RunWorkerState.WAITING_APPROVAL
        ))

        self.assertTrue(supervisor.has_active_workers())
        supervisor.request_cancel(run["id"])
        supervisor.wait(1)

    def test_waiting_slot_worker_remains_active(self) -> None:
        run, _ = self.store.enqueue_run(self.session["id"], "slot")
        supervisor = self._supervisor(_BlockingEngine)
        start = supervisor.prepare_next()
        assert start is not None
        handle = supervisor._handles[run["id"]]
        handle.state = RunWorkerState.WAITING_SLOT

        self.assertTrue(supervisor.has_active_workers())

        supervisor.release(start)
        _BlockingEngine.release.set()
        supervisor.wait(1)

    def test_handle_removed_only_after_worker_exit(self) -> None:
        run, _ = self.store.enqueue_run(self.session["id"], "block")
        supervisor = self._supervisor(_BlockingEngine)
        supervisor.schedule_next()
        self.assertTrue(_BlockingEngine.entered.wait(1))

        self.assertIn(run["id"], supervisor._handles)
        _BlockingEngine.release.set()
        supervisor.wait(1)
        self.assertNotIn(run["id"], supervisor._handles)

    def test_worker_finally_releases_resources(self) -> None:
        run, _ = self.store.enqueue_run(self.session["id"], "block")
        closed = threading.Event()
        cleaned = threading.Event()
        supervisor = RunSupervisor(
            self.store,
            lambda _model_id: ModelClientLease(object(), closed.set),
            lambda _message: None,
            lambda value: value,
            lambda: True,
            lambda: False,
            lambda: None,
            cleanup=cleaned.set,
            engine_factory=_BlockingEngine,
        )
        supervisor.schedule_next()
        self.assertTrue(_BlockingEngine.entered.wait(1))
        _BlockingEngine.release.set()
        supervisor.wait(1)

        self.assertTrue(closed.is_set())
        self.assertTrue(cleaned.is_set())
        self.assertNotIn(run["id"], supervisor._handles)


class ModelClientLeaseTests(unittest.TestCase):
    def test_model_configuration_rejected_while_run_active(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-phase5a-config-") as temporary:
            root = Path(temporary)
            data = root / "data"
            workspace = root / "workspace"
            data.mkdir(mode=0o700)
            workspace.mkdir()
            server = RuntimeServer(io.StringIO(), data, object())  # type: ignore[arg-type]
            server.store.initialize()
            server.model_config.initialize()
            server.initialized = True
            session = server.store.create_session(str(workspace))
            run, _ = server.store.enqueue_run(session["id"], "block")
            server.supervisor.engine_factory = _BlockingEngine
            _BlockingEngine.entered = threading.Event()
            _BlockingEngine.release = threading.Event()
            server.supervisor.schedule_next()
            self.assertTrue(_BlockingEngine.entered.wait(1))

            server.configure_model("client-config", {"apiKey": "sk-example-key-for-tests"})

            message = json.loads(server.output.getvalue().splitlines()[-1])
            self.assertEqual(message["error"]["data"]["code"], "RUN_ALREADY_ACTIVE")
            _BlockingEngine.release.set()
            server.supervisor.wait(1)
            server.store.cancel_run(run["id"])
            server.close()

    def test_model_configuration_rejected_while_waiting_approval(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-phase5a-config-") as temporary:
            root = Path(temporary)
            data = root / "data"
            workspace = root / "workspace"
            data.mkdir(mode=0o700)
            workspace.mkdir()
            server = RuntimeServer(io.StringIO(), data, object())  # type: ignore[arg-type]
            server.store.initialize()
            server.model_config.initialize()
            server.initialized = True
            session = server.store.create_session(str(workspace))
            run, _ = server.store.enqueue_run(session["id"], "approve")
            server.supervisor.engine_factory = _ApprovalWaitingEngine
            _ApprovalWaitingEngine.entered = threading.Event()
            server.supervisor.schedule_next()
            self.assertTrue(_ApprovalWaitingEngine.entered.wait(1))
            self.assertTrue(_wait_until(
                lambda: server.supervisor.handle_state(run["id"])
                is RunWorkerState.WAITING_APPROVAL
            ))

            server.configure_model("client-config", {"apiKey": "sk-example-key-for-tests"})

            message = json.loads(server.output.getvalue().splitlines()[-1])
            self.assertEqual(message["error"]["data"]["code"], "RUN_ALREADY_ACTIVE")
            server.supervisor.request_cancel(run["id"])
            server.supervisor.wait(1)
            server.close()

    def test_model_client_not_closed_before_worker_finishes(self) -> None:
        client = _CloseTrackingClient()
        with patch(
            "eidos_runtime.model.pydantic_ai_client.PydanticAIModelClient.deepseek",
            return_value=client,
        ):
            factory = ModelClientFactory("sk-example-key-for-tests")
            lease = factory.acquire("deepseek-v4-flash")
            with self.assertRaises(ModelClientInUseError):
                factory.close()
            self.assertFalse(client.closed)
            lease.close()
            factory.close()
            self.assertTrue(client.closed)

    def test_model_lease_kept_during_approval_wait(self) -> None:
        client = _CloseTrackingClient()
        with patch(
            "eidos_runtime.model.pydantic_ai_client.PydanticAIModelClient.deepseek",
            return_value=client,
        ):
            factory = ModelClientFactory("sk-example-key-for-tests")
            lease = factory.acquire("deepseek-v4-flash")
            self.assertEqual(factory.active_lease_count, 1)
            self.assertFalse(lease.closed)
            lease.close()
            factory.close()

    def test_model_lease_released_after_worker_exit(self) -> None:
        closed = threading.Event()
        lease = ModelClientLease(object(), closed.set)
        lease.close()
        self.assertTrue(closed.is_set())

    def test_model_lease_close_is_idempotent(self) -> None:
        closes = 0

        def close() -> None:
            nonlocal closes
            closes += 1

        lease = ModelClientLease(object(), close)
        lease.close()
        lease.close()
        self.assertEqual(closes, 1)


class ReliableCancelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-phase5a-cancel-")
        root = Path(self.temporary.name)
        data = root / "data"
        workspace = root / "workspace"
        data.mkdir(mode=0o700)
        workspace.mkdir()
        self.store = SessionStore(data)
        self.store.initialize()
        self.session = self.store.create_session(str(workspace))
        _CancelAwareEngine.entered = threading.Event()
        _CancelAwareEngine.cancel_seen = threading.Event()
        _CancelAwareEngine.allow_exit = threading.Event()
        _StuckEngine.entered = threading.Event()
        _StuckEngine.release = threading.Event()

    def tearDown(self) -> None:
        _CancelAwareEngine.allow_exit.set()
        _StuckEngine.release.set()
        self.store.close()
        self.temporary.cleanup()

    def _supervisor(self, engine_factory, *, cancel_timeout: float = 0.2):
        return RunSupervisor(
            self.store,
            lambda _model_id: ModelClientLease(object()),
            lambda _message: None,
            lambda value: value,
            lambda: True,
            lambda: False,
            lambda: None,
            engine_factory=engine_factory,
            cancel_timeout=cancel_timeout,
            shutdown_timeout=cancel_timeout,
        )

    def _started(self, engine_factory=_CancelAwareEngine):
        run, _ = self.store.enqueue_run(self.session["id"], "cancel")
        supervisor = self._supervisor(engine_factory)
        supervisor.schedule_next()
        self.assertTrue(engine_factory.entered.wait(1))
        return run, supervisor

    def test_cancel_does_not_commit_terminal_before_worker_exit(self) -> None:
        run, supervisor = self._started()
        result: list[dict[str, object]] = []
        worker = threading.Thread(
            target=lambda: result.append(supervisor.cancel_run(run["id"]))
        )
        worker.start()
        self.assertTrue(_CancelAwareEngine.cancel_seen.wait(1))

        self.assertNotEqual(self.store.read_run(run["id"])["status"], "canceled")
        self.assertTrue(worker.is_alive())
        _CancelAwareEngine.allow_exit.set()
        worker.join(1)
        self.assertEqual(result[0]["status"], "canceled")

    def test_cancel_during_model_stream(self) -> None:
        run, supervisor = self._started()
        _CancelAwareEngine.allow_exit.set()

        canceled = supervisor.cancel_run(run["id"])

        self.assertEqual(canceled["status"], "canceled")
        self.assertFalse(supervisor.has_active_workers())

    def test_cancel_during_approval_wait(self) -> None:
        run, _ = self.store.enqueue_run(self.session["id"], "approval")
        supervisor = self._supervisor(_ApprovalWaitingEngine)
        _ApprovalWaitingEngine.entered = threading.Event()
        supervisor.schedule_next()
        self.assertTrue(_ApprovalWaitingEngine.entered.wait(1))
        self.assertTrue(_wait_until(
            lambda: supervisor.handle_state(run["id"]) is RunWorkerState.WAITING_APPROVAL
        ))

        canceled = supervisor.cancel_run(run["id"])

        self.assertEqual(canceled["status"], "canceled")
        self.assertFalse(supervisor.has_active_workers())

    def test_cancel_during_finalization(self) -> None:
        run, supervisor = self._started()
        connection = self.store.connection
        assert connection is not None
        connection.execute(
            "UPDATE runs SET status = 'finalizing' WHERE id = ?", (run["id"],)
        )
        connection.commit()
        _CancelAwareEngine.allow_exit.set()

        canceled = supervisor.cancel_run(run["id"])

        self.assertEqual(canceled["status"], "canceled")

    def test_cancel_releases_waiting_worker(self) -> None:
        run, supervisor = self._started()
        connection = self.store.connection
        assert connection is not None
        connection.execute(
            "UPDATE execution_segments SET status = 'queued' WHERE run_id = ?",
            (run["id"],),
        )
        connection.execute(
            "UPDATE runs SET status = 'queued' WHERE id = ?",
            (run["id"],),
        )
        connection.commit()
        with supervisor.lock:
            supervisor._handles[run["id"]].state = RunWorkerState.WAITING_SLOT
            supervisor._active_slot_run_id = None
        _CancelAwareEngine.allow_exit.set()

        canceled = supervisor.cancel_run(run["id"])

        self.assertEqual(canceled["status"], "canceled")
        self.assertNotIn(run["id"], supervisor._handles)

    def test_cancel_timeout_does_not_report_canceled(self) -> None:
        run, supervisor = self._started(_StuckEngine)

        with self.assertRaises(RunCancelTimeout):
            supervisor.cancel_run(run["id"])

        current = self.store.read_run(run["id"])
        self.assertNotEqual(current["status"], "canceled")
        self.assertEqual(current["cancelFailureCode"], "RUN_CANCEL_TIMEOUT")
        _StuckEngine.release.set()
        supervisor.wait(1)

    def test_cancel_with_uncertain_side_effect_requires_reconciliation(self) -> None:
        run, supervisor = self._started()
        connection = self.store.connection
        assert connection is not None
        connection.execute(
            """
            UPDATE runs
            SET reconciliation_required = 1, side_effects_may_exist = 1
            WHERE id = ?
            """,
            (run["id"],),
        )
        connection.commit()
        _CancelAwareEngine.allow_exit.set()

        with self.assertRaises(RunReconciliationRequired):
            supervisor.cancel_run(run["id"])

        current = self.store.read_run(run["id"])
        self.assertEqual(current["status"], "interrupted")
        self.assertEqual(current["cancelFailureCode"], "RECONCILIATION_REQUIRED")


class ReliableShutdownTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-phase5a-shutdown-")
        root = Path(self.temporary.name)
        data = root / "data"
        self.workspace = root / "workspace"
        data.mkdir(mode=0o700)
        self.workspace.mkdir()
        self.store = SessionStore(data)
        self.store.initialize()
        self.session = self.store.create_session(str(self.workspace))
        _CancelAwareEngine.entered = threading.Event()
        _CancelAwareEngine.cancel_seen = threading.Event()
        _CancelAwareEngine.allow_exit = threading.Event()
        _StuckEngine.entered = threading.Event()
        _StuckEngine.release = threading.Event()

    def tearDown(self) -> None:
        _CancelAwareEngine.allow_exit.set()
        _StuckEngine.release.set()
        self.store.close()
        self.temporary.cleanup()

    def _supervisor(self, engine_factory, *, timeout: float = 0.2):
        return RunSupervisor(
            self.store,
            lambda _model_id: ModelClientLease(object()),
            lambda _message: None,
            lambda value: value,
            lambda: True,
            lambda: False,
            lambda: None,
            engine_factory=engine_factory,
            cancel_timeout=timeout,
            shutdown_timeout=timeout,
        )

    def _started(self, engine_factory=_CancelAwareEngine, *, timeout: float = 0.2):
        run, _ = self.store.enqueue_run(self.session["id"], "shutdown")
        supervisor = self._supervisor(engine_factory, timeout=timeout)
        supervisor.schedule_next()
        self.assertTrue(engine_factory.entered.wait(1))
        return run, supervisor

    def test_shutdown_rejects_new_runs(self) -> None:
        _run, supervisor = self._started(_StuckEngine, timeout=0.01)
        with self.assertRaises(RuntimeShutdownTimeout):
            supervisor.shutdown()

        queued, _ = self.store.enqueue_run(self.session["id"], "new")
        self.assertIsNone(supervisor.prepare_next())
        self.assertEqual(supervisor.lifecycle, RuntimeLifecycle.DRAINING)
        self.store.cancel_run(queued["id"])
        _StuckEngine.release.set()
        supervisor.wait(1)

    def test_shutdown_cancels_all_active_runs(self) -> None:
        run, supervisor = self._started()
        queued, _ = self.store.enqueue_run(self.session["id"], "queued")
        _CancelAwareEngine.allow_exit.set()

        supervisor.shutdown()

        self.assertEqual(self.store.read_run(run["id"])["status"], "canceled")
        self.assertEqual(self.store.read_run(queued["id"])["status"], "canceled")

    def test_shutdown_waits_for_all_workers(self) -> None:
        _run, supervisor = self._started(timeout=1)
        completed = threading.Event()
        thread = threading.Thread(target=lambda: (supervisor.shutdown(), completed.set()))
        thread.start()
        self.assertTrue(_CancelAwareEngine.cancel_seen.wait(1))
        self.assertFalse(completed.is_set())
        _CancelAwareEngine.allow_exit.set()
        thread.join(1)
        self.assertTrue(completed.is_set())

    def test_shutdown_does_not_close_live_model_client(self) -> None:
        run, supervisor = self._started(_StuckEngine, timeout=0.01)

        with self.assertRaises(RuntimeShutdownTimeout):
            supervisor.shutdown()

        self.assertTrue(supervisor.has_active_model_leases())
        self.assertNotEqual(self.store.read_run(run["id"])["status"], "canceled")
        _StuckEngine.release.set()
        supervisor.wait(1)

    def test_shutdown_cancels_pending_approvals(self) -> None:
        run, _ = self.store.enqueue_run(self.session["id"], "approval")
        supervisor = self._supervisor(_ApprovalWaitingEngine)
        _ApprovalWaitingEngine.entered = threading.Event()
        supervisor.schedule_next()
        self.assertTrue(_ApprovalWaitingEngine.entered.wait(1))

        supervisor.shutdown()

        self.assertEqual(self.store.read_run(run["id"])["status"], "canceled")
        connection = self.store.connection
        assert connection is not None
        self.assertIsNone(connection.execute(
            "SELECT 1 FROM approvals WHERE run_id = ? AND status = 'pending'",
            (run["id"],),
        ).fetchone())

    def test_shutdown_timeout_returns_error(self) -> None:
        _run, supervisor = self._started(_StuckEngine, timeout=0.01)

        with self.assertRaises(RuntimeShutdownTimeout):
            supervisor.shutdown()
        _StuckEngine.release.set()
        supervisor.wait(1)

    def test_shutdown_success_means_no_live_run_handles(self) -> None:
        _run, supervisor = self._started()
        _CancelAwareEngine.allow_exit.set()

        supervisor.shutdown()

        self.assertFalse(supervisor.has_active_workers())
        self.assertEqual(supervisor.lifecycle, RuntimeLifecycle.QUIESCENT)

    def test_shutdown_success_means_no_model_leases(self) -> None:
        _run, supervisor = self._started()
        _CancelAwareEngine.allow_exit.set()

        supervisor.shutdown()

        self.assertFalse(supervisor.has_active_model_leases())


class _CloseTrackingClient:
    closed = False

    @classmethod
    def deepseek(cls, _api_key: str, _model_id: str):
        return cls()

    def close(self) -> None:
        self.closed = True


def _wait_until(predicate, timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


if __name__ == "__main__":
    unittest.main()
