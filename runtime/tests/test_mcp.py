from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.extensions.mcp import (  # noqa: E402
    McpConnection,
    McpManager,
    McpUnavailable,
)
from eidos_runtime.extensions.plugins import PluginCatalog  # noqa: E402
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel  # noqa: E402
from eidos_runtime.runtime.resource_registry import (  # noqa: E402
    ResourceRegistry,
    RuntimeResourceKind,
)
from eidos_runtime.tools.registry import ToolRegistry  # noqa: E402


class McpManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-mcp-")
        root = Path(self.temporary.name)
        self.data = root / "data"
        self.workspace = root / "workspace"
        source = root / "plugin"
        self.data.mkdir(mode=0o700)
        self.workspace.mkdir()
        source.mkdir()
        fixture = Path(__file__).parent / "fixtures" / "mcp_fixture.py"
        (source / "server.py").write_bytes(fixture.read_bytes())
        (source / "plugin.json").write_text(json.dumps({
            "schemaVersion": 1,
            "id": "demo",
            "name": "Demo",
            "version": "1.0.0",
            "description": "MCP fixture",
            "skills": [],
            "mcpServers": [{
                "id": "fixture",
                "executable": sys.executable,
                "argv": ["server.py"],
                "envNames": [],
                "permissionProfile": "workspace_read",
                "startupTimeoutSeconds": 5,
                "toolTimeoutSeconds": 1,
                "enabled": True,
            }],
        }), encoding="utf-8")
        self.store = SessionStore(self.data)
        self.store.initialize()
        self.plugins = PluginCatalog(self.store)
        self.plugins.import_directory(source)
        self.plugins.set_enabled("demo", True)
        self.plugins.set_mcp_enabled("demo", "fixture", True)
        self.resources = ResourceRegistry()
        self.kernel = RuntimeAsyncKernel(resource_registry=self.resources)
        self.kernel.start()
        self.manager = McpManager(
            self.plugins,
            self.plugins.extension_snapshot(),
            self.workspace,
            sandbox=False,
            async_kernel=self.kernel,
            resource_registry=self.resources,
        )

    def tearDown(self) -> None:
        self.manager.close()
        self.kernel.close()
        self.assertEqual(self.resources.active_resources(), ())
        self.store.close()
        self.temporary.cleanup()

    def test_connection_is_a_service_on_the_shared_kernel(self) -> None:
        self.manager.start()

        connection = self.manager.connections[0]
        active = self.resources.active_resources()

        self.assertIs(connection.async_kernel, self.kernel)
        self.assertFalse(hasattr(connection, "thread"))
        self.assertFalse(hasattr(connection, "commands"))
        self.assertFalse(any(
            thread.name.startswith("eidos-mcp-")
            for thread in threading.enumerate()
        ))
        self.assertEqual(
            [resource.kind for resource in active].count(
                RuntimeResourceKind.MCP_CONNECTION
            ),
            1,
        )
        async_tasks = [
            resource
            for resource in active
            if resource.kind is RuntimeResourceKind.ASYNC_TASK
        ]
        self.assertEqual(len(async_tasks), 1)
        self.assertEqual(async_tasks[0].owner_id, "mcp:fixture")

    def test_two_connections_share_the_runtime_kernel(self) -> None:
        second = McpManager(
            self.plugins,
            self.plugins.extension_snapshot(),
            self.workspace,
            sandbox=False,
            async_kernel=self.kernel,
            resource_registry=self.resources,
        )
        try:
            self.manager.start()
            second.start()

            self.assertIs(
                self.manager.connections[0].async_kernel,
                second.connections[0].async_kernel,
            )
            self.assertEqual(
                sum(
                    resource.kind is RuntimeResourceKind.ASYNC_TASK
                    for resource in self.resources.active_resources()
                ),
                2,
            )
        finally:
            second.close()

    def test_startup_timeout_is_closed_and_releases_async_task(self) -> None:
        config = self.plugins.manifest("demo").mcp_servers[0].model_copy(
            update={
                "argv": ["server.py", "--startup-delay"],
                "startup_timeout_seconds": 1,
            }
        )
        connection = McpConnection(
            plugin_root=self.plugins.installed_root("demo"),
            runtime_root=self.data / "extensions" / "mcp-runtime",
            workspace_root=self.workspace,
            config=config,
            on_list_changed=lambda: None,
            async_kernel=self.kernel,
            sandbox=False,
            resource_registry=self.resources,
        )

        with self.assertRaisesRegex(McpUnavailable, "mcp_startup_timeout"):
            connection.start()

        self.assertFalse(any(
            resource.kind is RuntimeResourceKind.ASYNC_TASK
            for resource in self.resources.active_resources()
        ))

    def test_enabled_server_without_bound_kernel_fails_closed(self) -> None:
        manager = McpManager(
            self.plugins,
            self.plugins.extension_snapshot(),
            self.workspace,
            sandbox=False,
        )

        self.assertEqual(manager.start(), ())
        server = self.plugins.list_mcp_servers()[0]
        self.assertFalse(server["available"])
        self.assertEqual(server["errorCode"], "mcp_connection_lost")

    def test_discovers_namespaced_external_tools_and_calls_success(self) -> None:
        entries = self.manager.start()
        echo = next(value for value in entries if value.spec.name.endswith("__echo"))

        result = echo.adapter.execute(
            {"message": "hello"}, threading.Event()
        )

        self.assertEqual(echo.spec.side_effect, "external")
        self.assertTrue(echo.spec.approval_required)
        self.assertEqual(echo.spec.visibility, "deferred")
        self.assertEqual(echo.provenance.kind, "mcp")
        self.assertEqual(result["outcome"], "success")
        self.assertEqual(result["data"]["text"], "hello")
        registry = ToolRegistry.build(builtin_entries=(), external_entries=entries)
        self.assertNotIn("mcp__fixture__invalid", registry.names)
        self.assertTrue(any(value.name.endswith("__invalid") for value in registry.quarantined))

        refreshed = self.manager.refresh_if_changed()
        self.assertIsNotNone(refreshed)

    def test_maps_is_error_and_timeout_without_retry(self) -> None:
        entries = self.manager.start()
        failed = next(value for value in entries if value.spec.name.endswith("__fail"))
        slow = next(value for value in entries if value.spec.name.endswith("__slow"))

        failed_result = failed.adapter.execute({}, threading.Event())
        timeout_result = slow.adapter.execute({}, threading.Event())

        self.assertEqual(failed_result["code"], "mcp_tool_error")
        self.assertFalse(failed_result["sideEffectsMayExist"])
        self.assertEqual(timeout_result["code"], "mcp_tool_timeout")
        self.assertTrue(timeout_result["sideEffectsMayExist"])
        self.assertTrue(timeout_result["reconciliationRequired"])

    def test_preserves_structured_content(self) -> None:
        entries = self.manager.start()
        structured = next(
            value for value in entries if value.spec.name.endswith("__structured")
        )

        result = structured.adapter.execute({}, threading.Event())

        self.assertEqual(result["outcome"], "success")
        self.assertEqual(result["data"]["structuredContent"], {"answer": 42})

    def test_rejects_unsupported_content_and_cancel_never_sends(self) -> None:
        entries = self.manager.start()
        image = next(value for value in entries if value.spec.name.endswith("__image"))
        echo = next(value for value in entries if value.spec.name.endswith("__echo"))
        cancel = threading.Event()
        cancel.set()

        image_result = image.adapter.execute({}, threading.Event())
        canceled = echo.adapter.execute({"message": "no"}, cancel)

        self.assertEqual(image_result["code"], "mcp_content_unsupported")
        self.assertEqual(canceled["code"], "mcp_tool_canceled")
        self.assertTrue(canceled["sideEffectsMayExist"])

    def test_stdout_pollution_is_a_closed_uncertain_result(self) -> None:
        entries = self.manager.start()
        tool = next(value for value in entries if value.spec.name.endswith("__pollute"))

        result = tool.adapter.execute({}, threading.Event())

        self.assertEqual(result["code"], "mcp_stdout_pollution")
        self.assertTrue(result["sideEffectsMayExist"])

    def test_server_crash_is_not_retried(self) -> None:
        entries = self.manager.start()
        tool = next(value for value in entries if value.spec.name.endswith("__crash"))

        result = tool.adapter.execute({}, threading.Event())

        self.assertEqual(result["code"], "mcp_connection_lost")
        self.assertTrue(result["reconciliationRequired"])

    def test_in_flight_cancel_is_prompt_and_dead_connection_is_unavailable(self) -> None:
        entries = self.manager.start()
        slow = next(value for value in entries if value.spec.name.endswith("__slow"))
        cancel = threading.Event()
        timer = threading.Timer(0.1, cancel.set)
        timer.start()
        started = time.monotonic()

        canceled = slow.adapter.execute({}, cancel)
        elapsed = time.monotonic() - started
        self.manager.close()
        unavailable = slow.adapter.execute({}, threading.Event())

        timer.cancel()
        self.assertLess(elapsed, 0.8)
        self.assertEqual(canceled["code"], "mcp_tool_canceled")
        self.assertTrue(canceled["reconciliationRequired"])
        self.assertEqual(unavailable["code"], "tool_unavailable")
        self.assertFalse(unavailable["sideEffectsMayExist"])

    def test_close_terminates_server_process_group_children(self) -> None:
        entries = self.manager.start()
        tool = next(
            value for value in entries if value.spec.name.endswith("__spawn_child")
        )
        result = tool.adapter.execute({}, threading.Event())
        child_pid = int(result["data"]["text"])

        self.manager.close()

        for _ in range(20):
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            try:
                os.kill(child_pid, 9)
            except ProcessLookupError:
                pass
            self.fail("MCP child process survived connection close")


if __name__ == "__main__":
    unittest.main()
