from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.extensions.browser_plugin import BrowserLaunch
from eidos_runtime.extensions.contracts import McpServerConfigV1
from eidos_runtime.extensions.mcp import McpConnection, McpManager, McpUnavailable
from eidos_runtime.extensions.plugin_mcp import read_mcp_servers
from eidos_runtime.extensions.plugins import PluginCatalog
from eidos_runtime.infrastructure.browser_runtime import BrowserRuntime
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
from eidos_runtime.runtime.resource_registry import ResourceRegistry

BROWSER_ARGS = [
    "${BROWSER_MCP_ENTRY}", "--cdp-endpoint", "${BROWSER_CDP_ENDPOINT}",
    "--output-dir", "${BROWSER_OUTPUT_DIR}", "--image-responses", "${BROWSER_IMAGE_RESPONSES}",
]


class BrowserMcpLaunchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-browser-mcp-")
        root = Path(self.temporary.name).resolve()
        self.plugin = root / "plugin"
        self.workspace = root / "workspace"
        self.node_root = root / "resources/node"
        self.browser_root = root / "resources/browser"
        for path in (self.plugin, self.workspace, self.node_root, self.browser_root):
            path.mkdir(parents=True)
        self.resources = ResourceRegistry()
        self.kernel = RuntimeAsyncKernel(resource_registry=self.resources)
        self.launch = BrowserLaunch(
            runtime=BrowserRuntime(
                node=self.node_root / "bin/node", entry=self.browser_root / "node_modules/@playwright/mcp/cli.js",
                read_roots=(self.node_root, self.browser_root),
            ),
            variables={"BROWSER_CDP_ENDPOINT": "http://127.0.0.1:9333", "BROWSER_IMAGE_RESPONSES": "omit"},
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def connection(self, *, launch: BrowserLaunch | None, sandbox: bool = True, argv=None) -> McpConnection:
        config = McpServerConfigV1.model_validate({
            "id": "browser", "executable": "${RUNTIME_NODE}", "argv": BROWSER_ARGS if argv is None else argv,
            "permissionProfile": "browser",
        })
        return McpConnection(
            plugin_root=self.plugin, workspace_root=self.workspace, config=config,
            on_list_changed=lambda: None, async_kernel=self.kernel, sandbox=sandbox,
            resource_registry=self.resources, browser_launch=launch,
        )

    def test_launch_expands_verified_paths_inside_the_browser_profile(self) -> None:
        connection = self.connection(launch=self.launch)
        try:
            parameters = connection._prepare_parameters()
        finally:
            connection.close()
        arguments = parameters.args
        profile = arguments[arguments.index("-f") + 1]
        self.assertTrue(profile.endswith("mcp_browser.sbpl"))
        self.assertIn(f"-DNODE_RUNTIME_ROOT={self.node_root}", arguments)
        self.assertIn(f"-DBROWSER_RUNTIME_ROOT={self.browser_root}", arguments)
        command = arguments[arguments.index("--") + 1:]
        output = str(connection.runtime_root / "tmp" / "browser-output")
        self.assertEqual(command, [
            str(self.launch.runtime.node), str(self.launch.runtime.entry), "--cdp-endpoint", "http://127.0.0.1:9333",
            "--output-dir", output, "--image-responses", "omit",
        ])
        self.assertTrue(all("${" not in value for value in command))

    def test_browser_profile_requires_resolved_launch_inputs(self) -> None:
        for launch, argv in ((None, None), (self.launch, [*BROWSER_ARGS, "${UNKNOWN}"])):
            connection = self.connection(launch=launch, argv=argv)
            try:
                with self.assertRaises(McpUnavailable):
                    connection._prepare_parameters()
            finally:
                connection.close()

    def test_import_accepts_browser_variables_only_with_the_browser_profile(self) -> None:
        def declaration(profile: str) -> dict[str, bytes]:
            return {"mcp.json": json.dumps({"mcpServers": {"browser": {
                "type": "stdio", "command": "${RUNTIME_NODE}", "args": BROWSER_ARGS,
                "extensions": {"com.eidos": {"permissionProfile": profile}},
            }}}).encode()}

        servers, issues = read_mcp_servers(declaration("browser"), {}, portable=True)
        self.assertEqual([server.permission_profile for server in servers], ["browser"])
        self.assertEqual(issues, [])
        servers, issues = read_mcp_servers(declaration("connector"), {}, portable=True)
        self.assertEqual(servers, [])
        self.assertEqual(issues, ["mcp_settings_unsupported:browser"])


class BrowserProfileTrustTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-browser-trust-")
        root = Path(self.temporary.name)
        self.data = root / "data"
        self.data.mkdir(mode=0o700)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        source = root / "plugin"
        source.mkdir()
        (source / "plugin.json").write_text(json.dumps({
            "schemaVersion": 1, "id": "eidos-browser", "name": "Lookalike", "version": "1.0.0",
            "description": "Not the bundled package", "skills": [],
            "mcpServers": [{
                "id": "browser", "executable": sys.executable, "argv": ["server.py"], "envNames": [],
                "permissionProfile": "browser", "startupTimeoutSeconds": 5, "toolTimeoutSeconds": 5, "enabled": True,
            }],
        }), encoding="utf-8")
        self.store = SessionStore(self.data)
        self.store.initialize()
        self.plugins = PluginCatalog(self.store)
        self.plugins.import_directory(source)
        self.plugins.set_enabled("eidos-browser", True)
        self.plugins.set_mcp_enabled("eidos-browser", "browser", True)
        self.resources = ResourceRegistry()
        self.kernel = RuntimeAsyncKernel(resource_registry=self.resources)
        self.kernel.start()

    def tearDown(self) -> None:
        self.kernel.close()
        self.store.close()
        self.temporary.cleanup()

    def test_same_named_local_package_cannot_use_the_browser_profile(self) -> None:
        launched = []
        manager = McpManager(
            self.plugins, self.plugins.extension_snapshot(), self.workspace, sandbox=False,
            async_kernel=self.kernel, resource_registry=self.resources,
            browser_launch=lambda: launched.append(True),
        )
        with patch.object(McpConnection, "start", side_effect=AssertionError("must not start")):
            self.assertEqual(manager.start(), ())
        manager.close()
        self.assertEqual(launched, [])
        server = next(value for value in self.plugins.list_mcp_servers() if value["serverId"] == "browser")
        self.assertEqual(server.get("errorCode"), "mcp_permission_profile_unsupported")
