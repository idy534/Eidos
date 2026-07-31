from __future__ import annotations

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
    RuntimeAsyncKernel,
)
from eidos_runtime.runtime.resource_registry import (
    ResourceRegistry,
    RuntimeResourceKind,
)
from eidos_runtime.model.client import ScriptedModel
from eidos_runtime.protocol.server import RuntimeServer
from eidos_runtime.sandbox.seatbelt import SeatbeltSelfTestResult


class RuntimeAsyncKernelTests(unittest.TestCase):
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
