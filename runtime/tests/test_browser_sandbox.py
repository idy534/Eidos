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
EXTENSION_PROFILE = PROFILE.with_name("mcp_browser_extension.sbpl")


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

    def reachable_remote_probe(self) -> list[str]:
        """Return a connect probe that succeeds outside the sandbox, or skip the test."""
        # TEST-NET-1 is never this host. Only a reachable route proves denial.
        # macOS nc bounds the connect with -G; -w alone lets an unanswered SYN hang.
        remote = ["nc", "-z", "-G", "3", "-w", "3", "192.0.2.1", "443"]
        try:
            reachable = subprocess.run(remote, capture_output=True, timeout=20, check=False).returncode == 0
        except subprocess.TimeoutExpired:
            reachable = False
        if not reachable:
            self.skipTest("this machine has no route to a non-local address")
        return remote

    def run_sandboxed(self, command: str, profile: Path = PROFILE) -> subprocess.CompletedProcess[str]:
        return subprocess.run([
            SANDBOX_EXEC, "-f", str(profile),
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
        denied = self.run_sandboxed(" ".join(self.reachable_remote_probe()))
        self.assertNotEqual(denied.returncode, 0)

    def test_default_profile_cannot_listen_but_extension_profile_can_on_loopback(self) -> None:
        listen = "/usr/bin/nc -l 127.0.0.1 0 & pid=$!; sleep 0.5; kill -0 $pid && echo listening; kill $pid"
        self.assertNotIn("listening", self.run_sandboxed(listen).stdout)
        self.assertIn("listening", self.run_sandboxed(listen, EXTENSION_PROFILE).stdout)

    def test_extension_profile_reaches_the_broker_socket_but_not_the_internet(self) -> None:
        import tempfile
        # Unix socket paths are limited to 104 bytes.
        short = Path(tempfile.mkdtemp(prefix="eb-", dir="/private/tmp"))
        try:
            self.tmp = short
            socket_path = short / "connect-broker.sock"
            server = socket.socket(socket.AF_UNIX)
            server.bind(str(socket_path))
            server.listen(1)
            received: list[bytes] = []

            def accept() -> None:
                connection, _ = server.accept()
                received.append(connection.recv(4096))
                connection.close()

            thread = threading.Thread(target=accept, daemon=True)
            thread.start()
            sent = self.run_sandboxed(f"printf 'page\\n' | /usr/bin/nc -U {socket_path}", EXTENSION_PROFILE)
            thread.join(5)
            server.close()
            self.assertEqual(sent.returncode, 0, sent.stderr)
            self.assertEqual(received, [b"page\n"])
        finally:
            for child in short.iterdir():
                child.unlink()
            short.rmdir()
        remote = self.reachable_remote_probe()
        self.assertNotEqual(self.run_sandboxed(" ".join(remote), EXTENSION_PROFILE).returncode, 0)
