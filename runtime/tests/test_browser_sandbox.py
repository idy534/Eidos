from __future__ import annotations

from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest

import pytest

from eidos_runtime.sandbox.seatbelt import is_seatbelt_usable

pytestmark = pytest.mark.platform

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
PROFILE = Path(__file__).resolve().parents[1] / "eidos_runtime" / "sandbox" / "mcp_browser.sbpl"


@unittest.skipUnless(
    sys.platform == "darwin" and Path(SANDBOX_EXEC).is_file() and is_seatbelt_usable(),
    "browser Seatbelt tests require working macOS seatbelt",
)
class BrowserSeatbeltTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-browser-seatbelt-")
        root = Path(self.temporary.name).resolve()
        self.workspace = root / "workspace"
        self.home = root / "home"
        self.tmp = root / "tmp"
        self.runtime = root / "runtime"
        for path in (self.workspace, self.home, self.tmp, self.runtime):
            path.mkdir()
        (self.workspace / "upload.txt").write_text("upload\n")
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.bind(("0.0.0.0", 0))
        self.server.listen(8)
        self.port = self.server.getsockname()[1]
        self.accepted = 0
        threading.Thread(target=self._accept, daemon=True).start()

    def tearDown(self) -> None:
        self.server.close()
        self.temporary.cleanup()

    def _accept(self) -> None:
        while True:
            try:
                connection, _ = self.server.accept()
            except OSError:
                return
            self.accepted += 1
            connection.close()

    def run_sandboxed(self, command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([
            SANDBOX_EXEC, "-f", str(PROFILE),
            f"-DPLUGIN_ROOT={self.runtime}", f"-DWORKSPACE_ROOT={self.workspace}",
            f"-DSANDBOX_HOME={self.home}", f"-DSANDBOX_TMP={self.tmp}",
            f"-DNODE_RUNTIME_ROOT={self.runtime}", f"-DBROWSER_RUNTIME_ROOT={self.runtime}",
            "/bin/sh", "-c", command,
        ], cwd=self.home, env={"HOME": str(self.home), "TMPDIR": str(self.tmp), "PATH": "/usr/bin:/bin"},
            capture_output=True, text=True, timeout=20, check=False)

    def test_workspace_is_readable_but_not_writable(self) -> None:
        read = self.run_sandboxed(f"cat {self.workspace / 'upload.txt'}")
        write = self.run_sandboxed(f"echo changed > {self.workspace / 'upload.txt'}")
        scratch = self.run_sandboxed(f"echo ok > {self.tmp / 'out.txt'} && cat {self.tmp / 'out.txt'}")
        self.assertEqual(read.stdout, "upload\n")
        self.assertNotEqual(write.returncode, 0)
        self.assertEqual((self.workspace / "upload.txt").read_text(), "upload\n")
        self.assertEqual(scratch.stdout, "ok\n")

    def test_private_home_files_are_not_readable(self) -> None:
        secret = Path(self.temporary.name).resolve() / "secret.txt"
        secret.write_text("secret\n")
        result = self.run_sandboxed(f"cat {secret}")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("secret", result.stdout)

    def test_loopback_is_reachable_but_other_hosts_are_not(self) -> None:
        loopback = self.run_sandboxed(f"nc -z -w 3 127.0.0.1 {self.port}")
        self.assertEqual(loopback.returncode, 0, loopback.stderr)
        # TEST-NET-1 is never this host. Only a reachable route proves denial.
        remote = ["nc", "-z", "-w", "3", "192.0.2.1", "443"]
        if subprocess.run(remote, capture_output=True, timeout=20, check=False).returncode != 0:
            self.skipTest("this machine has no route to a non-local address")
        denied = self.run_sandboxed(" ".join(remote))
        self.assertNotEqual(denied.returncode, 0)
