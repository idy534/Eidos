from __future__ import annotations

import ast
import io
import inspect
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))


class AsyncOwnershipArchitectureTests(unittest.TestCase):
    def test_boundary_modules_do_not_reintroduce_private_async_owners(self) -> None:
        modules = (
            "extensions/mcp.py",
            "runtime/tool_runtime.py",
            "runtime/supervisor.py",
        )
        forbidden = (
            "anyio.run(",
            "asyncio.run(",
            "new_event_loop(",
            "ThreadPoolExecutor(",
            "queue.Queue(",
        )
        for relative_path in modules:
            source = (RUNTIME_ROOT / "eidos_runtime" / relative_path).read_text(
                encoding="utf-8"
            )
            with self.subTest(path=relative_path):
                self.assertTrue(all(term not in source for term in forbidden))

    def test_managed_task_registration_uses_no_busy_yield_loop(self) -> None:
        source = (RUNTIME_ROOT / "eidos_runtime/runtime/supervisor.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("while not registered.is_set():", source)
        self.assertNotIn("await anyio.sleep(0)", source)

    def test_mcp_managed_task_and_parallel_read_have_no_dedicated_threads(self) -> None:
        from eidos_runtime.extensions.mcp import McpConnection
        from eidos_runtime.runtime.supervisor import ManagedTask

        parallel_source = (RUNTIME_ROOT / "eidos_runtime/runtime/tool_runtime.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("Thread(", inspect.getsource(McpConnection))
        self.assertNotIn("thread", ManagedTask.__annotations__)
        self.assertNotIn("ThreadPoolExecutor", parallel_source)
        self.assertNotIn("Future", parallel_source)

    def test_runtime_server_initialization_owns_exactly_one_kernel(self) -> None:
        from eidos_runtime.protocol.server import RuntimeServer

        tree = ast.parse((RUNTIME_ROOT / "eidos_runtime/protocol/server.py").read_text(
            encoding="utf-8"
        ))
        initialize = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "initialize"
        )
        kernel_calls = [
            node for node in ast.walk(initialize)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "RuntimeAsyncKernel"
        ]
        self.assertEqual(len(kernel_calls), 1)
        self.assertIsNotNone(RuntimeServer)

    def test_shutdown_uses_explicit_resources_without_thread_or_tool_counters(self) -> None:
        from eidos_runtime.protocol.server import RuntimeServer

        supervisor_source = (RUNTIME_ROOT / "eidos_runtime/runtime/supervisor.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("threading.enumerate", inspect.getsource(RuntimeServer))
        self.assertNotIn("threading.enumerate", supervisor_source)
        self.assertNotIn("active_tool", supervisor_source)
        self.assertNotIn("active_tool", (RUNTIME_ROOT / "eidos_runtime/runtime/tool_runtime.py").read_text(
            encoding="utf-8"
        ))
        close_source = inspect.getsource(RuntimeServer.close)
        self.assertLess(
            close_source.index("self.supervisor.shutdown()"),
            close_source.rindex("self._close_async_kernel()"),
        )
        self.assertLess(
            close_source.index("self._close_model_factory()"),
            close_source.rindex("self._close_async_kernel()"),
        )

    def test_successful_runtime_shutdown_leaves_the_resource_registry_empty(self) -> None:
        from eidos_runtime.model.client import ScriptedModel
        from eidos_runtime.protocol.server import RuntimeServer
        from eidos_runtime.sandbox.seatbelt import SeatbeltSelfTestResult

        with tempfile.TemporaryDirectory(prefix="eidos-async-ownership-") as root:
            server = RuntimeServer(io.StringIO(), Path(root), ScriptedModel([]))
            with patch(
                "eidos_runtime.protocol.server.run_seatbelt_self_test",
                return_value=SeatbeltSelfTestResult(False, (), ("test",)),
            ):
                server.initialize("client-init", {
                    "client": {"name": "test", "version": "1"},
                    "protocolVersion": 1,
                })
            server.close()
            server.supervisor.resources.ensure_empty()
