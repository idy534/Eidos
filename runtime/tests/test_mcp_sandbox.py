from __future__ import annotations

import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from uuid import uuid4


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.extensions.mcp import McpManager  # noqa: E402
from eidos_runtime.extensions.mcp_environment import McpSocketDirectory  # noqa: E402
from eidos_runtime.extensions.mcp_outputs import McpOutputDirectory  # noqa: E402
from eidos_runtime.extensions.plugins import PluginCatalog  # noqa: E402
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel  # noqa: E402
from eidos_runtime.runtime.resource_registry import ResourceRegistry  # noqa: E402
from eidos_runtime.sandbox.seatbelt import (  # noqa: E402
    is_seatbelt_usable,
    python_runtime_policy_arguments,
    runtime_python_executable,
)


SANDBOX_EXEC = "/usr/bin/sandbox-exec"
SANDBOX_DIR = Path(__file__).resolve().parents[1] / "eidos_runtime" / "sandbox"
PYTHON = str(runtime_python_executable())


@unittest.skipUnless(
    sys.platform == "darwin" and Path(SANDBOX_EXEC).is_file() and is_seatbelt_usable(),
    "MCP Seatbelt tests require working macOS seatbelt",
)
class McpSeatbeltTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-mcp-seatbelt-")
        root = Path(self.temporary.name).resolve()
        self.plugin = root / "plugin"
        self.workspace = root / "workspace"
        self.sandbox_home = root / "sandbox-home"
        self.sandbox_tmp = root / "sandbox-tmp"
        self.state = root / "state"
        for path in (
            self.plugin, self.workspace, self.sandbox_home,
            self.sandbox_tmp, self.state,
        ):
            path.mkdir(mode=0o700)
        (self.plugin / "plugin.txt").write_text("plugin", encoding="utf-8")
        (self.workspace / "workspace.txt").write_text("workspace", encoding="utf-8")
        (self.state / "secret.txt").write_text("state-secret", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_connector_allows_network_but_denies_workspace_and_state(self) -> None:
        self.assertTrue(self._run("connector", "open(%r).read()" % str(self.plugin / "plugin.txt")))
        self.assertFalse(self._run("connector", "open(%r).read()" % str(self.workspace / "workspace.txt")))
        self.assertFalse(self._run("connector", "open(%r).read()" % str(self.state / "secret.txt")))
        self.assertFalse(self._run("connector", "import os;os.listdir(%r)" % str(Path.home())))
        self.assertFalse(self._run("connector", "open(%r,'w').write('x')" % str(self.workspace / "new.txt")))
        self.assertTrue(self._network_allowed("connector"))

    def test_connector_resolves_network_hosts(self) -> None:
        self.assertTrue(self._run(
            "connector",
            "import socket;socket.getaddrinfo('registry.npmjs.org',443)",
        ))

    def test_workspace_read_allows_reads_but_denies_write_and_network(self) -> None:
        self.assertTrue(self._run("workspace_read", "open(%r).read()" % str(self.plugin / "plugin.txt")))
        self.assertTrue(self._run("workspace_read", "open(%r).read()" % str(self.workspace / "workspace.txt")))
        self.assertFalse(self._run("workspace_read", "open(%r,'w').write('x')" % str(self.workspace / "new.txt")))
        self.assertFalse(self._run("workspace_read", "import os;os.listdir(%r)" % str(Path.home())))
        self.assertFalse(self._network_allowed("workspace_read"))
        self.assertTrue(self._run(
            "workspace_read",
            "import subprocess,sys;subprocess.check_call([sys.executable,'-c',%r])"
            % ("open(%r).read()" % str(self.workspace / "workspace.txt")),
        ))

    def test_official_client_runs_fixture_through_workspace_read_profile(self) -> None:
        self._check_official_client("workspace_read")

    def test_official_client_binds_socket_through_connector_profile(self) -> None:
        self._check_official_client("connector")

    def test_short_socket_directory_does_not_grant_neighbor_writes(self) -> None:
        owned = McpSocketDirectory.create()
        neighbor = McpSocketDirectory.create()
        try:
            for profile in ("connector", "workspace_read"):
                with self.subTest(profile=profile):
                    self.assertTrue(self._run(profile, "open(%r,'w').write('owned')" % str(owned.path / "allowed"), socket_root=owned.path))
                    self.assertFalse(self._run(profile, "open(%r,'w').write('neighbor')" % str(neighbor.path / "denied"), socket_root=owned.path))
            self.assertFalse((neighbor.path / "denied").exists())
        finally:
            owned.cleanup()
            neighbor.cleanup()

    def _check_official_client(self, profile: str) -> None:
        source = self.state.parent / "source-plugin"
        source.mkdir()
        fixture = Path(__file__).parent / "fixtures" / "mcp_fixture.py"
        (source / "server.py").write_bytes(fixture.read_bytes())
        (source / "plugin.json").write_text(json.dumps({
            "schemaVersion": 1,
            "id": "native",
            "name": "Native",
            "version": "1.0.0",
            "description": "Native fixture",
            "skills": [],
            "mcpServers": [{
                "id": "fixture",
                "executable": PYTHON,
                "argv": ["server.py"],
                "envNames": [],
                "permissionProfile": profile,
                "startupTimeoutSeconds": 15,
                "toolTimeoutSeconds": 2,
                "enabled": True,
            }],
        }), encoding="utf-8")
        store = SessionStore(self.state)
        store.initialize()
        plugins = PluginCatalog(store)
        plugins.import_directory(source)
        plugins.set_enabled("native", True)
        plugins.set_mcp_enabled("native", "fixture", True)
        resources = ResourceRegistry()
        kernel = RuntimeAsyncKernel(resource_registry=resources)
        kernel.start()
        manager = McpManager(
            plugins,
            plugins.extension_snapshot(),
            self.workspace,
            async_kernel=kernel,
            sandbox=True,
            resource_registry=resources,
        )
        try:
            entries = manager.start()
            echo = next(value for value in entries if value.spec.name.endswith("__echo"))
            result = echo.adapter.execute({"message": "native"}, threading.Event())
            self.assertEqual(result["data"]["text"], "native")
            environment_tool = next(value for value in entries if value.spec.name.endswith("__environment"))
            environment_result = environment_tool.adapter.execute({}, threading.Event())
            environment = json.loads(environment_result["data"]["text"])
            socket_root = Path(environment["socketDir"])
            output_root = Path(environment["outputDir"])
            self.assertEqual(manager.output_roots(), (output_root,))
            self.assertEqual(Path(environment["home"]), output_root.parent / "h")
            self.assertEqual(Path(environment["tmpdir"]), output_root / "t")
            self.assertLessEqual(len(environment["socketDir"].encode("utf-8")), 48)
            if profile == "connector":
                probe = next(value for value in entries if value.spec.name.endswith("__socket_probe"))
                result = probe.adapter.execute({}, threading.Event())
                self.assertEqual(result["outcome"], "success")
                self.assertLessEqual(json.loads(result["data"]["text"])["socketBytes"], 103)
        finally:
            manager.close()
            kernel.close()
            self.assertEqual(resources.active_resources(), ())
            store.close()
        self.assertFalse(socket_root.exists())
        self.assertTrue(output_root.is_dir())
        self.assertTrue((output_root.parent / "h").is_dir())
        self.assertTrue((output_root / "t").is_dir())

    def test_output_directory_writes_do_not_grant_neighbor_run_access(self) -> None:
        current = McpOutputDirectory.create("manual", "browser", str(uuid4()))
        neighbor = McpOutputDirectory.create("manual", "browser", str(uuid4()))
        other_service = McpOutputDirectory.create("manual", "other", str(uuid4()))
        self.sandbox_home = current.environment_path("h")
        other_home = other_service.environment_path("h")
        neighbor_temp = neighbor.environment_path("t")
        for profile in ("connector", "workspace_read"):
            with self.subTest(profile=profile):
                self.assertTrue(self._run(
                    profile, "open(%r,'w').write('state')" % str(self.sandbox_home / "cache.txt"),
                    output_root=current.path,
                ))
                self.assertTrue(self._run(
                    profile, "open(%r,'w').write('output')" % str(current.path / "result.txt"),
                    output_root=current.path,
                ))
                self.assertFalse(self._run(
                    profile, "open(%r,'w').write('denied')" % str(neighbor.path / "result.txt"),
                    output_root=current.path,
                ))
                for denied in (other_home / "state.txt", neighbor_temp / "temporary.txt"):
                    self.assertFalse(self._run(
                        profile, "open(%r,'w').write('denied')" % str(denied),
                        output_root=current.path,
                    ))
        self.assertEqual((current.path / "result.txt").read_text(), "output")
        self.assertFalse((neighbor.path / "result.txt").exists())

    def _run(
        self, profile: str, code: str, *,
        output_root: Path | None = None, socket_root: Path | None = None,
    ) -> bool:
        command = [
            SANDBOX_EXEC,
            "-f", str(SANDBOX_DIR / f"mcp_{profile}.sbpl"),
            f"-DPLUGIN_ROOT={self.plugin}",
            f"-DWORKSPACE_ROOT={self.workspace}",
            f"-DSANDBOX_HOME={self.sandbox_home}",
            f"-DSANDBOX_TMP={self.sandbox_tmp}",
            f"-DMCP_OUTPUT_ROOT={output_root or self.sandbox_tmp}",
            f"-DMCP_SOCKET_ROOT={socket_root or self.sandbox_tmp}",
            *python_runtime_policy_arguments(),
            "--", PYTHON, "-c", code,
        ]
        try:
            result = subprocess.run(
                command,
                cwd=self.plugin,
                env={
                    "HOME": str(self.sandbox_home),
                    "TMPDIR": str(self.sandbox_tmp),
                    "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                },
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False
        return result.returncode == 0

    def _network_allowed(self, profile: str) -> bool:
        listener = socket.socket()
        try:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            return self._run(
                profile,
                "import socket;s=socket.socket();s.settimeout(1);s.connect(('127.0.0.1',%d));s.close()"
                % port,
            )
        finally:
            listener.close()


if __name__ == "__main__":
    unittest.main()
