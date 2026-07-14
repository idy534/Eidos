from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.seatbelt import SeatbeltSelfTestResult  # noqa: E402
from eidos_runtime.server import RuntimeServer  # noqa: E402


def run_runtime(
    messages: list[str], data_directory: Path | None = None
) -> subprocess.CompletedProcess[str]:
    if data_directory is None:
        with tempfile.TemporaryDirectory(prefix="eidos-runtime-test-") as directory:
            return run_runtime(messages, Path(directory))

    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(RUNTIME_ROOT)
    environment["EIDOS_DATA_DIR"] = str(data_directory)
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
        request = {
            "jsonrpc": "2.0",
            "id": "client-1",
            "method": "initialize",
            "params": {
                "client": {"name": "eidos-desktop", "version": "0.1.0"},
                "protocolVersion": 1,
            },
        }

        with tempfile.TemporaryDirectory(prefix="eidos-data-") as data_directory:
            server = RuntimeServer(output, Path(data_directory))
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
            server.store.close()

        self_test.assert_called_once_with()
        response = json.loads(output.getvalue())
        self.assertTrue(response["result"]["capabilities"]["runShell"])
        self.assertFalse(response["result"]["capabilities"]["modelConfigured"])
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
                        "capabilities": {
                            "runShell": True,
                            "modelConfigured": False,
                        },
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

    def test_session_persists_across_runtime_restart(self) -> None:
        with (
            tempfile.TemporaryDirectory(prefix="eidos-data-") as data_directory,
            tempfile.TemporaryDirectory(prefix="eidos-workspace-") as workspace,
        ):
            first = run_runtime(
                [
                    initialize_message("client-1"),
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": "client-2",
                            "method": "session/create",
                            "params": {"workspaceRoot": workspace},
                        }
                    ),
                    shutdown_message("client-3"),
                ],
                Path(data_directory),
            )
            created = json.loads(first.stdout.splitlines()[1])["result"]

            second = run_runtime(
                [
                    initialize_message("client-1"),
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": "client-2",
                            "method": "session/list",
                            "params": {},
                        }
                    ),
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": "client-3",
                            "method": "session/read",
                            "params": {"sessionId": created["id"]},
                        }
                    ),
                    shutdown_message("client-4"),
                ],
                Path(data_directory),
            )
            responses = [json.loads(line) for line in second.stdout.splitlines()]

            self.assertEqual(created["workspaceRoot"], str(Path(workspace).resolve()))
            self.assertEqual(responses[1]["result"], {"items": [created]})
            self.assertEqual(
                responses[2]["result"],
                {"session": created, "runs": [], "items": []},
            )
            self.assertEqual((Path(data_directory) / "eidos.db").stat().st_mode & 0o777, 0o600)

    def test_session_list_uses_an_opaque_cursor(self) -> None:
        with (
            tempfile.TemporaryDirectory(prefix="eidos-data-") as data_directory,
            tempfile.TemporaryDirectory(prefix="eidos-workspace-") as workspace,
        ):
            messages = [initialize_message("client-1")]
            for index in range(3):
                messages.append(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": f"client-{index + 2}",
                            "method": "session/create",
                            "params": {"workspaceRoot": workspace},
                        }
                    )
                )
            messages.extend(
                [
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": "client-5",
                            "method": "session/list",
                            "params": {"limit": 2},
                        }
                    ),
                    shutdown_message("client-6"),
                ]
            )
            first_page_run = run_runtime(messages, Path(data_directory))
            first_page = json.loads(first_page_run.stdout.splitlines()[4])["result"]

            second_page_run = run_runtime(
                [
                    initialize_message("client-1"),
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": "client-2",
                            "method": "session/list",
                            "params": {"limit": 2, "cursor": first_page["nextCursor"]},
                        }
                    ),
                    shutdown_message("client-3"),
                ],
                Path(data_directory),
            )
            second_page = json.loads(second_page_run.stdout.splitlines()[1])["result"]

            self.assertEqual(len(first_page["items"]), 2)
            self.assertNotIn("nextCursor", second_page)
            self.assertEqual(len(second_page["items"]), 1)
            self.assertTrue(
                set(item["id"] for item in first_page["items"]).isdisjoint(
                    item["id"] for item in second_page["items"]
                )
            )

    def test_session_create_rejects_a_symlink_workspace_without_persisting(self) -> None:
        with (
            tempfile.TemporaryDirectory(prefix="eidos-data-") as data_directory,
            tempfile.TemporaryDirectory(prefix="eidos-workspace-") as workspace_parent,
        ):
            real_workspace = Path(workspace_parent) / "real"
            linked_workspace = Path(workspace_parent) / "linked"
            real_workspace.mkdir()
            linked_workspace.symlink_to(real_workspace, target_is_directory=True)

            completed = run_runtime(
                [
                    initialize_message("client-1"),
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": "client-2",
                            "method": "session/create",
                            "params": {"workspaceRoot": str(linked_workspace)},
                        }
                    ),
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": "client-3",
                            "method": "session/list",
                            "params": {},
                        }
                    ),
                    shutdown_message("client-4"),
                ],
                Path(data_directory),
            )
            responses = [json.loads(line) for line in completed.stdout.splitlines()]

            self.assertEqual(
                responses[1]["error"]["data"]["code"],
                "WORKSPACE_BOUNDARY_VIOLATION",
            )
            self.assertEqual(responses[2]["result"], {"items": []})

    def test_session_list_rejects_an_invalid_cursor_safely(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-data-") as data_directory:
            completed = run_runtime(
                [
                    initialize_message("client-1"),
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": "client-2",
                            "method": "session/list",
                            "params": {"cursor": "%"},
                        }
                    ),
                    shutdown_message("client-3"),
                ],
                Path(data_directory),
            )
            response = json.loads(completed.stdout.splitlines()[1])

            self.assertEqual(response["error"]["code"], -32602)
            self.assertNotIn("Traceback", completed.stdout)

    def test_session_read_returns_safe_not_found_error(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-data-") as data_directory:
            completed = run_runtime(
                [
                    initialize_message("client-1"),
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": "client-2",
                            "method": "session/read",
                            "params": {"sessionId": "00000000-0000-4000-8000-000000000000"},
                        }
                    ),
                    shutdown_message("client-3"),
                ],
                Path(data_directory),
            )
            response = json.loads(completed.stdout.splitlines()[1])

            self.assertEqual(response["error"]["code"], -32000)
            self.assertEqual(
                response["error"]["data"],
                {"code": "RESOURCE_NOT_FOUND", "retryable": False},
            )

    def test_model_configuration_is_private_and_loaded_after_restart(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-data-") as data_directory:
            configured = run_runtime(
                [
                    initialize_message("client-1"),
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": "client-2",
                            "method": "model/configure",
                            "params": {"apiKey": "sk-example-key-for-tests"},
                        }
                    ),
                    shutdown_message("client-3"),
                ],
                Path(data_directory),
            )
            configured_response = json.loads(configured.stdout.splitlines()[1])
            self.assertEqual(
                configured_response["result"],
                {
                    "provider": "deepseek",
                    "model": "deepseek-v4-flash",
                    "configured": True,
                },
            )
            self.assertNotIn("sk-example", configured.stdout)

            restarted = run_runtime(
                [initialize_message("client-1"), shutdown_message("client-2")],
                Path(data_directory),
            )
            initialize_response = json.loads(restarted.stdout.splitlines()[0])
            self.assertTrue(
                initialize_response["result"]["capabilities"]["modelConfigured"]
            )


def initialize_message(request_id: str) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "initialize",
            "params": {
                "client": {"name": "eidos-desktop", "version": "0.1.0"},
                "protocolVersion": 1,
            },
        }
    )


def shutdown_message(request_id: str) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "runtime/shutdown",
            "params": {},
        }
    )


if __name__ == "__main__":
    unittest.main()
