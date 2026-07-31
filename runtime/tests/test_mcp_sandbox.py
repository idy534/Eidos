from __future__ import annotations

import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.extensions.mcp import McpManager  # noqa: E402
from eidos_runtime.extensions.plugins import PluginCatalog  # noqa: E402
from eidos_runtime.sandbox.seatbelt import is_seatbelt_usable  # noqa: E402


SANDBOX_EXEC = "/usr/bin/sandbox-exec"
SANDBOX_DIR = Path(__file__).resolve().parents[1] / "eidos_runtime" / "sandbox"
PYTHON = "/Library/Developer/CommandLineTools/usr/bin/python3"


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
                "permissionProfile": "workspace_read",
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
        manager = McpManager(
            plugins, plugins.extension_snapshot(), self.workspace, sandbox=True
        )
        try:
            entries = manager.start()
            echo = next(value for value in entries if value.spec.name.endswith("__echo"))
            result = echo.adapter.execute({"message": "native"}, threading.Event())
            self.assertEqual(result["data"]["text"], "native")
        finally:
            manager.close()
            store.close()

    def _run(self, profile: str, code: str) -> bool:
        command = [
            SANDBOX_EXEC,
            "-f", str(SANDBOX_DIR / f"mcp_{profile}.sbpl"),
            f"-DPLUGIN_ROOT={self.plugin}",
            f"-DWORKSPACE_ROOT={self.workspace}",
            f"-DSANDBOX_HOME={self.sandbox_home}",
            f"-DSANDBOX_TMP={self.sandbox_tmp}",
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
