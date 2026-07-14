from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.seatbelt import SeatbeltSelfTestResult  # noqa: E402
from eidos_runtime.server import RuntimeServer  # noqa: E402


def run_runtime(messages: list[str]) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(RUNTIME_ROOT)
    return subprocess.run(
        [sys.executable, "-m", "eidos_runtime"],
        input="\n".join(messages) + "\n",
        capture_output=True,
        text=True,
        env=environment,
        timeout=5,
        check=False,
    )


class RuntimeProtocolTests(unittest.TestCase):
    def test_initialize_runs_seatbelt_self_test_without_enabling_shell(self) -> None:
        output = io.StringIO()
        server = RuntimeServer(output)
        request = {
            "jsonrpc": "2.0",
            "id": "client-1",
            "method": "initialize",
            "params": {
                "client": {"name": "eidos-desktop", "version": "0.1.0"},
                "protocolVersion": 1,
            },
        }

        with (
            patch(
                "eidos_runtime.server.run_seatbelt_self_test",
                return_value=SeatbeltSelfTestResult(
                    available=True,
                    passed_checks=("workspace_write",),
                    failures=(),
                ),
            ) as self_test,
            self.assertLogs("eidos.runtime", level="INFO") as logs,
        ):
            server.handle(request)

        self_test.assert_called_once_with()
        response = json.loads(output.getvalue())
        self.assertFalse(response["result"]["capabilities"]["runShell"])
        self.assertTrue(any("Seatbelt self-test passed" in line for line in logs.output))

    def test_initialize_then_shutdown_keeps_stdout_protocol_only(self) -> None:
        completed = run_runtime(
            [
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": "client-1",
                        "method": "initialize",
                        "params": {
                            "client": {"name": "eidos-desktop", "version": "0.1.0"},
                            "protocolVersion": 1,
                        },
                    }
                ),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": "client-2",
                        "method": "runtime/shutdown",
                        "params": {},
                    }
                ),
            ]
        )

        self.assertEqual(completed.returncode, 0)
        stdout_messages = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual(
            stdout_messages,
            [
                {
                    "jsonrpc": "2.0",
                    "id": "client-1",
                    "result": {
                        "protocolVersion": 1,
                        "runtimeVersion": "0.1.0",
                        "capabilities": {"runShell": False},
                    },
                },
                {"jsonrpc": "2.0", "id": "client-2", "result": {}},
            ],
        )
        self.assertIn("Runtime initialized", completed.stderr)
        self.assertNotIn("Runtime initialized", completed.stdout)

    def test_business_request_before_initialize_is_rejected_safely(self) -> None:
        completed = run_runtime(
            [
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": "client-1",
                        "method": "session/list",
                        "params": {},
                    }
                ),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": "client-2",
                        "method": "runtime/shutdown",
                        "params": {},
                    }
                ),
            ]
        )

        first_response = json.loads(completed.stdout.splitlines()[0])
        self.assertEqual(first_response["error"]["code"], -32000)
        self.assertEqual(
            first_response["error"]["data"],
            {"code": "RUNTIME_NOT_INITIALIZED", "retryable": False},
        )
        self.assertNotIn("Traceback", completed.stdout)

    def test_invalid_json_returns_parse_error_without_stdout_traceback(self) -> None:
        completed = run_runtime(
            [
                "not-json",
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": "client-1",
                        "method": "runtime/shutdown",
                        "params": {},
                    }
                ),
            ]
        )

        first_response = json.loads(completed.stdout.splitlines()[0])
        self.assertEqual(first_response["error"]["code"], -32700)
        self.assertIsNone(first_response["id"])
        self.assertNotIn("Traceback", completed.stdout)


if __name__ == "__main__":
    unittest.main()
