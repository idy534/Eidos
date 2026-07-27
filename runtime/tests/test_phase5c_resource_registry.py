from __future__ import annotations

import io
from pathlib import Path
import sys
import tempfile
import threading
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.runtime.resource_registry import (  # noqa: E402
    ResourceRegistry,
    RuntimeResourceKind,
    RuntimeResourceState,
)
from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.model.pydantic_ai_client import ModelClientLease  # noqa: E402
from eidos_runtime.protocol.server import RuntimeServer  # noqa: E402
from eidos_runtime.runtime.state_machine import RuntimeLifecycle  # noqa: E402
from eidos_runtime.runtime.supervisor import (  # noqa: E402
    RunSupervisor,
    RuntimeShutdownTimeout,
)


class ResourceRegistryTests(unittest.TestCase):
    def test_resource_owner_is_preserved_in_diagnostics(self) -> None:
        registry = ResourceRegistry()
        resource = registry.register(
            RuntimeResourceKind.RUN_WORKER, owner_id="run-1"
        )

        diagnostic = registry.active_resources()[0]

        self.assertEqual(diagnostic.resource_id, resource.resource_id)
        self.assertEqual(diagnostic.owner_id, "run-1")
        self.assertEqual(diagnostic.kind, RuntimeResourceKind.RUN_WORKER)

    def test_double_close_is_idempotent(self) -> None:
        registry = ResourceRegistry()
        closed = 0

        def close() -> None:
            nonlocal closed
            closed += 1

        resource = registry.register(
            RuntimeResourceKind.MANAGED_TASK,
            owner_id="task-1",
            close=close,
        )

        resource.close()
        resource.close()

        self.assertEqual(closed, 1)
        self.assertEqual(registry.active_resources(), ())

    def test_resource_failure_remains_visible_until_closed(self) -> None:
        registry = ResourceRegistry()
        resource = registry.register(
            RuntimeResourceKind.MODEL_LOOP, owner_id="model-1"
        )

        resource.fail("MODEL_SHUTDOWN_TIMEOUT")

        diagnostic = registry.active_resources()[0]
        self.assertEqual(diagnostic.state, RuntimeResourceState.FAILED)
        self.assertEqual(
            diagnostic.diagnostic_code, "MODEL_SHUTDOWN_TIMEOUT"
        )
        resource.close()
        self.assertEqual(registry.active_resources(), ())

    def test_resource_registration_and_release_are_thread_safe(self) -> None:
        registry = ResourceRegistry()
        gate = threading.Barrier(17)

        def register_and_close(index: int) -> None:
            resource = registry.register(
                RuntimeResourceKind.TOOL_EXECUTION,
                owner_id=f"tool-{index}",
            )
            gate.wait()
            resource.close()

        threads = [
            threading.Thread(target=register_and_close, args=(index,))
            for index in range(16)
        ]
        for thread in threads:
            thread.start()
        gate.wait()
        for thread in threads:
            thread.join(1)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(registry.active_resources(), ())

    def test_shutdown_success_means_resource_registry_empty(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-registry-") as root:
            data = Path(root) / "data"
            data.mkdir(mode=0o700)
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
            )

            supervisor.shutdown()

            self.assertEqual(supervisor.resources.active_resources(), ())
            store.close()

    def test_resource_failure_prevents_closed_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-registry-") as root:
            data = Path(root) / "data"
            data.mkdir(mode=0o700)
            server = RuntimeServer(io.StringIO(), data)
            server.store.initialize()
            server.initialized = True
            resource = server.supervisor.resources.register(
                RuntimeResourceKind.MCP_CONNECTION, owner_id="server-1"
            )
            resource.fail("MCP_CANCEL_TIMEOUT")

            with self.assertRaisesRegex(
                RuntimeShutdownTimeout, "RUNTIME_SHUTDOWN_TIMEOUT"
            ):
                server.close()

            self.assertNotEqual(
                server.supervisor.lifecycle, RuntimeLifecycle.CLOSED
            )
            self.assertIsNotNone(server.store.connection)
            resource.close()
            server.store.close()


if __name__ == "__main__":
    unittest.main()
