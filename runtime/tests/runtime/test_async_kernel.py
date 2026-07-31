from __future__ import annotations

from concurrent.futures import CancelledError
import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import anyio

from eidos_runtime.runtime.async_kernel import (
    AsyncKernelCloseError,
    AsyncKernelClosedError,
    AsyncKernelState,
    AsyncTaskState,
    RuntimeAsyncKernel,
)
from eidos_runtime.runtime.resource_registry import (
    ResourceRegistry,
    RuntimeResourceKind,
    RuntimeResourceState,
)
from eidos_runtime.model.client import ScriptedModel
from eidos_runtime.protocol.server import RuntimeServer
from eidos_runtime.sandbox.seatbelt import SeatbeltSelfTestResult


class RuntimeAsyncKernelTests(unittest.TestCase):
    def test_owned_tasks_run_concurrently_and_retain_bounded_diagnostics(
        self,
    ) -> None:
        resources = ResourceRegistry()
        kernel = RuntimeAsyncKernel(
            resource_registry=resources,
            max_task_diagnostics=2,
        )
        kernel.start()
        entered = threading.Event()
        release = threading.Event()
        entered_count = 0
        entered_lock = threading.Lock()

        async def work(name: str) -> str:
            nonlocal entered_count
            with entered_lock:
                entered_count += 1
                if entered_count == 2:
                    entered.set()
            while not release.is_set():
                await anyio.sleep(0.001)
            return name

        first = kernel.start_task(
            work,
            "first",
            owner_id="owner-1",
            deadline=12.5,
        )
        second = kernel.start_task(work, "second", owner_id="owner-2")

        self.assertTrue(entered.wait(timeout=1.0))
        task_resources = [
            diagnostic
            for diagnostic in resources.active_resources()
            if diagnostic.kind is RuntimeResourceKind.ASYNC_TASK
        ]
        self.assertEqual(len(task_resources), 2)
        first_resource = next(
            diagnostic
            for diagnostic in task_resources
            if diagnostic.task_id == first.task_id
        )
        self.assertEqual(first_resource.owner_id, "owner-1")
        self.assertEqual(first_resource.state, RuntimeResourceState.RUNNING)
        self.assertEqual(first_resource.deadline, 12.5)

        release.set()
        self.assertTrue(first.wait(1.0))
        self.assertTrue(second.wait(1.0))
        self.assertEqual(first.result(), "first")
        self.assertEqual(second.result(), "second")
        self.assertEqual(first.state, AsyncTaskState.COMPLETED)
        self.assertEqual(second.state, AsyncTaskState.COMPLETED)
        self.assertIsNone(first.exception())
        self.assertCountEqual(
            [diagnostic.task_id for diagnostic in kernel.recent_task_diagnostics()],
            [first.task_id, second.task_id],
        )
        kernel.close()
        resources.ensure_empty()

    def test_service_returns_started_value_and_keeps_running(self) -> None:
        resources = ResourceRegistry()
        kernel = RuntimeAsyncKernel(resource_registry=resources)
        kernel.start()
        release = threading.Event()

        async def service(*, task_status) -> str:
            task_status.started({"status": "ready"})
            while not release.is_set():
                await anyio.sleep(0.001)
            return "stopped"

        task, started = kernel.start_service(service, owner_id="service-1")

        self.assertEqual(started, {"status": "ready"})
        self.assertEqual(task.state, AsyncTaskState.RUNNING)
        release.set()
        self.assertTrue(task.wait(1.0))
        self.assertEqual(task.result(), "stopped")
        kernel.close()
        resources.ensure_empty()

    def test_service_startup_failure_is_recorded_and_propagated(self) -> None:
        resources = ResourceRegistry()
        kernel = RuntimeAsyncKernel(resource_registry=resources)
        kernel.start()

        async def service(*, task_status) -> None:
            del task_status
            raise ValueError("startup failed")

        with self.assertRaisesRegex(ValueError, "startup failed"):
            kernel.start_service(service, owner_id="service-1")

        diagnostics = kernel.recent_task_diagnostics()
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0].owner_id, "service-1")
        self.assertEqual(diagnostics[0].state, AsyncTaskState.FAILED)
        self.assertEqual(diagnostics[0].diagnostic_code, "ASYNC_TASK_START_FAILED")
        self.assertEqual(
            [resource.kind for resource in resources.active_resources()],
            [RuntimeResourceKind.ASYNC_KERNEL],
        )
        kernel.close()
        resources.ensure_empty()

    def test_task_execution_exception_is_owned_by_the_handle(self) -> None:
        resources = ResourceRegistry()
        kernel = RuntimeAsyncKernel(resource_registry=resources)
        kernel.start()

        async def fail() -> None:
            raise ValueError("task failed")

        task = kernel.start_task(fail, owner_id="task-owner")

        self.assertTrue(task.wait(1.0))
        self.assertEqual(task.state, AsyncTaskState.FAILED)
        self.assertIsInstance(task.exception(), ValueError)
        with self.assertRaisesRegex(ValueError, "task failed"):
            task.result()
        self.assertEqual(
            kernel.recent_task_diagnostics()[0].diagnostic_code,
            "ASYNC_TASK_FAILED",
        )
        self.assertEqual(
            [resource.kind for resource in resources.active_resources()],
            [RuntimeResourceKind.ASYNC_KERNEL],
        )
        kernel.close()
        resources.ensure_empty()

    def test_task_can_be_actively_canceled(self) -> None:
        resources = ResourceRegistry()
        kernel = RuntimeAsyncKernel(resource_registry=resources)
        kernel.start()
        entered = threading.Event()

        async def wait_forever() -> None:
            entered.set()
            await anyio.sleep_forever()

        task = kernel.start_task(wait_forever, owner_id="task-owner")
        self.assertTrue(entered.wait(timeout=1.0))

        self.assertTrue(task.cancel())
        self.assertTrue(task.wait(1.0))

        self.assertEqual(task.state, AsyncTaskState.CANCELED)
        self.assertTrue(task.done())
        with self.assertRaises(CancelledError):
            task.result()
        kernel.close()
        resources.ensure_empty()

    def test_close_cancels_remaining_tasks_and_rejects_new_tasks(self) -> None:
        resources = ResourceRegistry()
        kernel = RuntimeAsyncKernel(resource_registry=resources)
        kernel.start()
        entered = threading.Event()

        async def wait_forever() -> None:
            entered.set()
            await anyio.sleep_forever()

        task = kernel.start_task(wait_forever, owner_id="task-owner")
        self.assertTrue(entered.wait(timeout=1.0))

        kernel.close()
        kernel.close()

        self.assertTrue(task.done())
        self.assertEqual(task.state, AsyncTaskState.CANCELED)
        self.assertEqual(kernel.state, AsyncKernelState.CLOSED)
        resources.ensure_empty()
        with self.assertRaises(AsyncKernelClosedError):
            kernel.start_task(wait_forever, owner_id="late-owner")

    def test_close_reports_task_shutdown_timeout_without_faking_stop(self) -> None:
        resources = ResourceRegistry()
        kernel = RuntimeAsyncKernel(
            resource_registry=resources,
            task_shutdown_timeout=0.05,
        )
        kernel.start()
        entered = threading.Event()
        release = threading.Event()

        async def ignore_cancel_until_released() -> None:
            with anyio.CancelScope(shield=True):
                entered.set()
                while not release.is_set():
                    await anyio.sleep(0.001)
            await anyio.sleep(0)

        task = kernel.start_task(
            ignore_cancel_until_released,
            owner_id="stubborn-owner",
            deadline=99.0,
        )
        self.assertTrue(entered.wait(timeout=1.0))

        with self.assertRaisesRegex(
            AsyncKernelCloseError,
            "ASYNC_KERNEL_TASK_SHUTDOWN_TIMEOUT",
        ):
            kernel.close()

        self.assertEqual(kernel.state, AsyncKernelState.FAILED)
        self.assertEqual(task.state, AsyncTaskState.CANCEL_REQUESTED)
        diagnostic = next(
            resource
            for resource in resources.active_resources()
            if resource.kind is RuntimeResourceKind.ASYNC_TASK
        )
        self.assertEqual(diagnostic.task_id, task.task_id)
        self.assertEqual(diagnostic.state, RuntimeResourceState.FAILED)
        self.assertEqual(
            diagnostic.diagnostic_code,
            "ASYNC_KERNEL_TASK_SHUTDOWN_TIMEOUT",
        )

        release.set()
        self.assertTrue(task.wait(1.0))
        kernel.close()
        self.assertEqual(kernel.state, AsyncKernelState.CLOSED)
        resources.ensure_empty()

    def test_task_completion_racing_close_does_not_deadlock(self) -> None:
        resources = ResourceRegistry()
        kernel = RuntimeAsyncKernel(
            resource_registry=resources,
            task_shutdown_timeout=1.0,
        )
        kernel.start()
        release = threading.Event()

        async def work() -> None:
            while not release.is_set():
                await anyio.sleep(0.001)

        tasks = [
            kernel.start_task(work, owner_id=f"task-{index}")
            for index in range(32)
        ]
        failures: list[BaseException] = []

        def close() -> None:
            try:
                kernel.close()
            except BaseException as error:
                failures.append(error)

        closer = threading.Thread(target=close)
        closer.start()
        release.set()
        closer.join(timeout=2.0)

        self.assertFalse(closer.is_alive())
        self.assertEqual(failures, [])
        self.assertTrue(all(task.done() for task in tasks))
        self.assertEqual(kernel.state, AsyncKernelState.CLOSED)
        resources.ensure_empty()

    def test_recent_task_diagnostics_are_bounded(self) -> None:
        kernel = RuntimeAsyncKernel(max_task_diagnostics=2)
        kernel.start()

        async def work(value: int) -> int:
            return value

        tasks = []
        for value in range(3):
            task = kernel.start_task(work, value, owner_id=f"owner-{value}")
            self.assertTrue(task.wait(1.0))
            tasks.append(task)

        self.assertEqual(
            [diagnostic.task_id for diagnostic in kernel.recent_task_diagnostics()],
            [tasks[1].task_id, tasks[2].task_id],
        )
        kernel.close()

    def test_start_close_and_resource_lifecycle_are_idempotent(self) -> None:
        resources = ResourceRegistry()
        kernel = RuntimeAsyncKernel(resource_registry=resources)

        self.assertEqual(kernel.state, AsyncKernelState.NEW)
        kernel.start()
        first_portal_id = kernel.portal_identity
        kernel.start()

        self.assertEqual(kernel.state, AsyncKernelState.RUNNING)
        self.assertEqual(kernel.portal_identity, first_portal_id)
        self.assertEqual(
            [resource.kind for resource in resources.active_resources()],
            [RuntimeResourceKind.ASYNC_KERNEL],
        )

        kernel.close()
        kernel.close()

        self.assertEqual(kernel.state, AsyncKernelState.CLOSED)
        resources.ensure_empty()

    def test_call_propagates_async_exception_and_rejects_after_close(self) -> None:
        kernel = RuntimeAsyncKernel()
        kernel.start()

        async def raises() -> None:
            raise ValueError("model failure")

        with self.assertRaisesRegex(ValueError, "model failure"):
            kernel.call(raises)
        kernel.close()

        with self.assertRaises(AsyncKernelClosedError):
            kernel.call(raises)

    def test_start_failure_is_recorded_on_the_kernel_resource(self) -> None:
        resources = ResourceRegistry()
        kernel = RuntimeAsyncKernel(resource_registry=resources)
        with patch(
            "eidos_runtime.runtime.async_kernel.start_blocking_portal",
            side_effect=RuntimeError("portal failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "portal failed"):
                kernel.start()

        self.assertEqual(kernel.state, AsyncKernelState.FAILED)
        diagnostic = resources.active_resources()[0]
        self.assertEqual(diagnostic.kind, RuntimeResourceKind.ASYNC_KERNEL)
        self.assertEqual(diagnostic.diagnostic_code, "ASYNC_KERNEL_START_FAILED")
        kernel.close()
        resources.ensure_empty()

    def test_close_failure_is_recorded_on_the_kernel_resource(self) -> None:
        class FailingPortal:
            def call(self, _function) -> None:
                raise RuntimeError("portal stop failed")

        class PortalContext:
            def __enter__(self) -> FailingPortal:
                return FailingPortal()

            def __exit__(self, *_args) -> None:
                return None

        resources = ResourceRegistry()
        kernel = RuntimeAsyncKernel(resource_registry=resources)
        with patch(
            "eidos_runtime.runtime.async_kernel.start_blocking_portal",
            return_value=PortalContext(),
        ):
            kernel.start()
        with self.assertRaisesRegex(
            AsyncKernelCloseError, "ASYNC_KERNEL_SHUTDOWN_FAILED"
        ):
            kernel.close()

        self.assertEqual(kernel.state, AsyncKernelState.FAILED)
        diagnostic = resources.active_resources()[0]
        self.assertEqual(diagnostic.diagnostic_code, "ASYNC_KERNEL_SHUTDOWN_FAILED")

    def test_calls_from_two_threads_run_concurrently(self) -> None:
        kernel = RuntimeAsyncKernel()
        kernel.start()
        entered = threading.Event()
        entered_count = 0
        entered_lock = threading.Lock()
        release = threading.Event()
        results: list[str] = []
        failures: list[BaseException] = []

        async def work(name: str) -> str:
            nonlocal entered_count
            with entered_lock:
                entered_count += 1
                if entered_count == 2:
                    entered.set()
            while not release.is_set():
                await anyio.sleep(0.001)
            return name

        def call(name: str) -> None:
            try:
                results.append(kernel.call(work, name))
            except BaseException as error:
                failures.append(error)

        first = threading.Thread(target=call, args=("first",))
        second = threading.Thread(target=call, args=("second",))
        first.start()
        second.start()
        self.assertTrue(entered.wait(timeout=1.0))
        release.set()
        first.join(timeout=1.0)
        second.join(timeout=1.0)
        kernel.close()

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(failures, [])
        self.assertCountEqual(results, ["first", "second"])

    def test_runtime_server_owns_one_kernel_until_shutdown(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-async-kernel-server-") as root:
            server = RuntimeServer(io.StringIO(), Path(root), ScriptedModel([]))
            with patch(
                "eidos_runtime.protocol.server.run_seatbelt_self_test",
                return_value=SeatbeltSelfTestResult(False, (), ("test",)),
            ):
                server.initialize("client-init", {
                    "client": {"name": "test", "version": "1"},
                    "protocolVersion": 1,
                })
            kernel = server.async_kernel
            self.assertIsNotNone(kernel)
            assert kernel is not None
            self.assertEqual(kernel.state, AsyncKernelState.RUNNING)
            self.assertIs(server.supervisor.async_kernel, kernel)
            self.assertEqual(
                [resource.kind for resource in server.supervisor.resources.active_resources()],
                [RuntimeResourceKind.ASYNC_KERNEL],
            )

            server.close()

            self.assertIsNone(server.async_kernel)
            self.assertEqual(kernel.state, AsyncKernelState.CLOSED)
            server.supervisor.resources.ensure_empty()

    def test_model_reconfiguration_reuses_runtime_kernel(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-async-kernel-config-") as root:
            server = RuntimeServer(io.StringIO(), Path(root), ScriptedModel([]))
            with patch(
                "eidos_runtime.protocol.server.run_seatbelt_self_test",
                return_value=SeatbeltSelfTestResult(False, (), ("test",)),
            ):
                server.initialize("client-init", {
                    "client": {"name": "test", "version": "1"},
                    "protocolVersion": 1,
                })
            kernel = server.async_kernel
            server.configure_model("client-config", {
                "apiKey": "sk-example-key-for-tests",
            })

            self.assertIs(server.async_kernel, kernel)
            self.assertEqual(
                [resource.kind for resource in server.supervisor.resources.active_resources()],
                [RuntimeResourceKind.ASYNC_KERNEL],
            )
            server.close()

    def test_failed_runtime_initialization_closes_the_started_kernel(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-async-kernel-init-") as root:
            server = RuntimeServer(io.StringIO(), Path(root), ScriptedModel([]))
            with patch(
                "eidos_runtime.protocol.server.ModelGateway",
                side_effect=RuntimeError("gateway failed"),
            ):
                server.initialize("client-init", {
                    "client": {"name": "test", "version": "1"},
                    "protocolVersion": 1,
                })

            self.assertIsNone(server.async_kernel)
            server.supervisor.resources.ensure_empty()
            server.close()


if __name__ == "__main__":
    unittest.main()
