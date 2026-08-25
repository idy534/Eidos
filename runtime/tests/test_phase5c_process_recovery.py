from __future__ import annotations

import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import time
import unittest


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_STATE_TIMEOUT_SECONDS = 15
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.db.invariants import verify_runtime_invariants  # noqa: E402
from eidos_runtime.db.storage import SessionStore  # noqa: E402


def _request(request_id: str, method: str, params: dict[str, object]) -> bytes:
    return (
        json.dumps({
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        })
        + "\n"
    ).encode()


def _init_repository(repository: Path) -> None:
    commands = (
        ("git", "init", "-q", "-b", "main"),
        ("git", "config", "user.email", "eidos-tests@example.com"),
        ("git", "config", "user.name", "Eidos Tests"),
    )
    for command in commands:
        subprocess.run(command, cwd=repository, check=True, capture_output=True)
    (repository / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(
        ("git", "add", "README.md"),
        cwd=repository,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ("git", "commit", "-qm", "initial"),
        cwd=repository,
        check=True,
        capture_output=True,
    )


class RuntimeProcessRecoveryTests(unittest.TestCase):
    def test_kill_during_real_approval_recovers_persisted_facts(self) -> None:
        with (
            tempfile.TemporaryDirectory(prefix="eidos-process-data-") as data,
            tempfile.TemporaryDirectory(prefix="eidos-process-workspace-") as workspace,
        ):
            _init_repository(Path(workspace))
            environment = os.environ.copy()
            environment.update({
                "PYTHONPATH": str(RUNTIME_ROOT),
                "EIDOS_DATA_DIR": data,
                "EIDOS_FAKE_MODEL": "network-shell",
            })
            process = subprocess.Popen(
                [sys.executable, "-m", "eidos_runtime"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
            )
            assert process.stdin is not None and process.stdout is not None
            selector = selectors.DefaultSelector()
            selector.register(process.stdout, selectors.EVENT_READ)

            def send(request_id: str, method: str, params: dict[str, object]) -> None:
                process.stdin.write(_request(request_id, method, params))
                process.stdin.flush()

            def read_until(predicate) -> dict[str, object]:
                deadline = time.monotonic() + PROTOCOL_STATE_TIMEOUT_SECONDS
                while time.monotonic() < deadline:
                    if not selector.select(timeout=0.1):
                        if process.poll() is not None:
                            break
                        continue
                    line = process.stdout.readline()
                    if not line:
                        break
                    message = json.loads(line)
                    if predicate(message):
                        return message
                returncode = process.poll()
                stderr_tail = ""
                if returncode is not None and process.stderr is not None:
                    stderr_tail = process.stderr.read().decode(errors="replace")[-2000:]
                self.fail(
                    "runtime did not reach expected protocol state within "
                    f"{PROTOCOL_STATE_TIMEOUT_SECONDS}s; returncode={returncode}; "
                    f"stderr={stderr_tail!r}"
                )

            try:
                send("client-init", "initialize", {
                    "client": {"name": "test", "version": "1"},
                    "protocolVersion": 1,
                })
                read_until(lambda message: message.get("id") == "client-init")
                send(
                    "client-session",
                    "session/create",
                    {"workspaceRoot": workspace},
                )
                session = read_until(
                    lambda message: message.get("id") == "client-session"
                )["result"]
                send("client-run", "run/start", {
                    "sessionId": session["id"],
                    "userInput": "run a command with network access",
                    "modelId": "deepseek-v4-flash",
                })
                read_until(
                    lambda message: message.get("method") == "item/requestApproval"
                )
                process.kill()
                process.wait(timeout=5)
            finally:
                selector.close()
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                if process.stdin is not None:
                    process.stdin.close()
                if process.stdout is not None:
                    process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()

            restarted = subprocess.run(
                [sys.executable, "-m", "eidos_runtime"],
                input=(
                    _request("client-init", "initialize", {
                        "client": {"name": "test", "version": "1"},
                        "protocolVersion": 1,
                    })
                    + _request("client-shutdown", "runtime/shutdown", {})
                ),
                capture_output=True,
                env=environment,
                timeout=10,
                check=False,
            )
            self.assertEqual(restarted.returncode, 0, restarted.stderr.decode())

            store = SessionStore(Path(data))
            store.initialize()
            try:
                assert store.connection is not None
                verify_runtime_invariants(store.connection)
                self.assertEqual(
                    store.connection.execute(
                        """
                        SELECT COUNT(*) FROM approvals
                        WHERE status = 'pending'
                        """
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    store.connection.execute(
                        """
                        SELECT COUNT(*) FROM event_outbox
                        WHERE status = 'pending'
                        """
                    ).fetchone()[0],
                    0,
                )
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
