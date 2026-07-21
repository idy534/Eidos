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
from eidos_runtime.extensions.mcp import McpManager  # noqa: E402
from eidos_runtime.extensions.plugins import PluginCatalog  # noqa: E402
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
        self.manager = McpManager(
            self.plugins,
            self.plugins.extension_snapshot(),
            self.workspace,
            sandbox=False,
        )

    def tearDown(self) -> None:
        self.manager.close()
        self.store.close()
        self.temporary.cleanup()

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
