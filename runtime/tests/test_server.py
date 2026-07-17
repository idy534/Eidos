from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_V1_FIXTURE = RUNTIME_ROOT.parent / "protocol" / "fixtures" / "v1.json"
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.model import ModelResponse, ModelToolCall, ScriptedModel  # noqa: E402
from eidos_runtime.seatbelt import SeatbeltSelfTestResult  # noqa: E402
from eidos_runtime.server import RuntimeServer, valid_request_id  # noqa: E402
from eidos_runtime.storage import (  # noqa: E402
    ContextLimitExceeded,
    SessionStore,
    WorkspaceBoundaryError,
)


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
    def test_waiting_approval_releases_execution_slot_and_requeues_fifo(self) -> None:
        with (
            tempfile.TemporaryDirectory(prefix="eidos-data-") as data_directory,
            tempfile.TemporaryDirectory(prefix="eidos-workspace-a-") as workspace_a,
            tempfile.TemporaryDirectory(prefix="eidos-workspace-b-") as workspace_b,
        ):
            output = io.StringIO()
            model = ScriptedModel([
                ModelResponse(tool_calls=(ModelToolCall(
                    "call-write", "write_file", {"path": "new.txt", "content": "candidate\n"},
                ),)),
                ModelResponse(text="second completed"),
                ModelResponse(text="first continued"),
            ])
            server = RuntimeServer(output, Path(data_directory), model)
            with patch(
                "eidos_runtime.server.run_seatbelt_self_test",
                return_value=SeatbeltSelfTestResult(False, (), ("test",)),
            ):
                server.handle({
                    "jsonrpc": "2.0", "id": "client-init", "method": "initialize",
                    "params": {"client": {"name": "test", "version": "1"}, "protocolVersion": 1},
                })
            first_session = server.store.create_session(workspace_a)
            second_session = server.store.create_session(workspace_b)
            first, _ = server.store.enqueue_run(first_session["id"], "write a file")
            second, _ = server.store.enqueue_run(second_session["id"], "answer now")
            server._schedule_next()

            approval_id = ""
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                messages = [json.loads(line) for line in output.getvalue().splitlines()]
                approval = next((message for message in messages if message.get("method") == "item/requestApproval"), None)
                if approval is not None:
                    approval_id = approval["id"]
                    break
                time.sleep(0.01)
            self.assertTrue(approval_id)

            while time.monotonic() < deadline and server.store.read_run(second["id"])["status"] != "succeeded":
                time.sleep(0.01)
            self.assertEqual(server.store.read_run(second["id"])["status"], "succeeded")
            self.assertEqual(server.store.read_run(first["id"])["status"], "waiting_approval")

            server.handle({
                "jsonrpc": "2.0", "id": approval_id,
                "result": {"decision": "reject", "feedback": "use another approach"},
            })
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and server.store.read_run(first["id"])["status"] != "succeeded":
                time.sleep(0.01)
            self.assertEqual(server.store.read_run(first["id"])["status"], "succeeded")
            server.close()

    def test_oversized_request_id_is_rejected_without_stopping_runtime(self) -> None:
        oversized_id = "client-" + "x" * 122
        completed = run_runtime(
            [
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": oversized_id,
                        "method": "session/list",
                        "params": {},
                    }
                ),
                shutdown_message("client-1"),
            ]
        )

        responses = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(responses[0]["error"]["code"], -32600)
        self.assertIsNone(responses[0]["id"])
        self.assertEqual(responses[1], {"jsonrpc": "2.0", "id": "client-1", "result": {}})

    def test_request_id_accepts_only_the_client_ascii_namespace(self) -> None:
        self.assertTrue(valid_request_id("client-request_1.test"))
        for invalid in (
            "client-",
            "client-request:1",
            "client-测试",
            "client-\ud800",
            "server-request-1",
            "client-request 1",
        ):
            with self.subTest(invalid=invalid):
                self.assertFalse(valid_request_id(invalid))

    def test_worker_failure_while_waiting_for_approval_releases_active_run(self) -> None:
        with (
            tempfile.TemporaryDirectory(prefix="eidos-data-") as data_directory,
            tempfile.TemporaryDirectory(prefix="eidos-workspace-") as workspace,
        ):
            output = io.StringIO()
            model = ScriptedModel(
                [
                    ModelResponse(
                        tool_calls=(
                            ModelToolCall(
                                "call-write",
                                "write_file",
                                {"path": "new.txt", "content": "candidate\n"},
                            ),
                        )
                    )
                ]
            )
            server = RuntimeServer(output, Path(data_directory), model)
            server.store.initialize()
            session = server.store.create_session(workspace)
            run, _ = server.store.create_run(session["id"], "create new.txt")

            def fail_approval(_params: object, _cancel: object) -> object:
                raise RuntimeError("approval channel failed")

            server.request_approval = fail_approval  # type: ignore[method-assign]
            start_gate = threading.Event()
            start_gate.set()
            with self.assertLogs("eidos.runtime", level="ERROR"):
                server._run_worker(run["id"], threading.Event(), start_gate)

            failed = server.store.read_run(run["id"])
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["errorCode"], "INTERNAL_ERROR")
            replacement, _ = server.store.create_run(session["id"], "try again")
            self.assertEqual(replacement["status"], "running")
            server.store.cancel_run(replacement["id"])
            server.store.close()

    def test_shared_v1_vectors_match_runtime_envelopes(self) -> None:
        vectors = json.loads(PROTOCOL_V1_FIXTURE.read_text(encoding="utf-8"))
        output = io.StringIO()

        with tempfile.TemporaryDirectory(prefix="eidos-data-") as data_directory:
            server = RuntimeServer(output, Path(data_directory))
            with patch(
                "eidos_runtime.server.run_seatbelt_self_test",
                return_value=SeatbeltSelfTestResult(
                    available=False,
                    passed_checks=(),
                    failures=("fixture",),
                ),
            ):
                server.handle(vectors["initialize"]["request"])
            server.store.close()

        self.assertEqual(vectors["protocolVersion"], 1)
        self.assertEqual(
            json.loads(output.getvalue()),
            vectors["initialize"]["response"],
        )
        self.assertEqual(
            vectors["notInitializedError"]["response"]["error"]["data"],
            {"code": "RUNTIME_NOT_INITIALIZED", "retryable": False},
        )

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
        self.assertEqual(len(stdout_messages), 2)
        self.assertEqual(stdout_messages[0]["jsonrpc"], "2.0")
        self.assertEqual(stdout_messages[0]["id"], "client-1")
        self.assertEqual(stdout_messages[0]["result"]["protocolVersion"], 1)
        self.assertEqual(stdout_messages[0]["result"]["runtimeVersion"], "0.2.0")
        self.assertIsInstance(
            stdout_messages[0]["result"]["capabilities"]["runShell"], bool
        )
        self.assertFalse(
            stdout_messages[0]["result"]["capabilities"]["modelConfigured"]
        )
        self.assertEqual(
            stdout_messages[1],
            {"jsonrpc": "2.0", "id": "client-2", "result": {}},
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
                {"session": created, "runs": [], "items": [], "throughEventId": 1},
            )
            self.assertEqual((Path(data_directory) / "eidos.db").stat().st_mode & 0o777, 0o600)

    def test_session_rejects_workspace_containing_runtime_data(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-overlap-") as root_directory:
            root = Path(root_directory)
            data_directory = root / ".eidos"
            completed = run_runtime(
                [
                    initialize_message("client-1"),
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": "client-2",
                            "method": "session/create",
                            "params": {"workspaceRoot": str(root)},
                        }
                    ),
                    shutdown_message("client-3"),
                ],
                data_directory,
            )

            response = json.loads(completed.stdout.splitlines()[1])
            self.assertEqual(
                response["error"]["data"],
                {"code": "WORKSPACE_BOUNDARY_VIOLATION", "retryable": False},
            )

    def test_legacy_session_overlapping_runtime_data_cannot_start_run(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-legacy-overlap-") as root_directory:
            root = Path(root_directory)
            data = root / ".eidos"
            safe_workspace = root / "safe"
            safe_workspace.mkdir()
            store = SessionStore(data)
            store.initialize()
            try:
                session = store.create_session(str(safe_workspace))
                metadata = root.stat()
                assert store.connection is not None
                store.connection.execute(
                    """
                    UPDATE sessions
                    SET workspace_root = ?, workspace_dev = ?,
                        workspace_inode = ?, workspace_uid = ?
                    WHERE id = ?
                    """,
                    (
                        str(root),
                        metadata.st_dev,
                        metadata.st_ino,
                        metadata.st_uid,
                        session["id"],
                    ),
                )
                store.connection.commit()

                with self.assertRaises(WorkspaceBoundaryError):
                    store.create_run(session["id"], "must not start")
            finally:
                store.close()

    def test_session_snapshot_stays_below_protocol_message_limit(self) -> None:
        with (
            tempfile.TemporaryDirectory(prefix="eidos-snapshot-data-") as data,
            tempfile.TemporaryDirectory(prefix="eidos-snapshot-workspace-") as workspace,
        ):
            store = SessionStore(Path(data))
            store.initialize()
            try:
                session = store.create_session(workspace)
                latest_run_id = ""
                for index in range(20):
                    run, _ = store.create_run(
                        session["id"], f"{index}:" + "x" * (64 * 1024 - 3)
                    )
                    latest_run_id = str(run["id"])
                    store.fail_run(latest_run_id, "TEST_COMPLETE")

                snapshot = store.read_session_snapshot(session["id"])
            finally:
                store.close()

            encoded = json.dumps(
                snapshot, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            self.assertLessEqual(len(encoded), 1024 * 1024)
            self.assertEqual(snapshot["runs"][-1]["id"], latest_run_id)

    def test_model_context_fails_instead_of_silently_dropping_history(self) -> None:
        with (
            tempfile.TemporaryDirectory(prefix="eidos-context-data-") as data,
            tempfile.TemporaryDirectory(prefix="eidos-context-workspace-") as workspace,
        ):
            store = SessionStore(Path(data))
            store.initialize()
            try:
                session = store.create_session(workspace)
                for index in range(13):
                    run, _ = store.create_run(
                        session["id"], f"{index}:" + "x" * (64 * 1024 - 3)
                    )
                    store.fail_run(run["id"], "TEST_COMPLETE")

                with self.assertRaises(ContextLimitExceeded):
                    store.model_context(session["id"])
            finally:
                store.close()

    def test_oversized_json_escaped_item_does_not_block_snapshot_pagination(self) -> None:
        with (
            tempfile.TemporaryDirectory(prefix="eidos-snapshot-data-") as data,
            tempfile.TemporaryDirectory(prefix="eidos-snapshot-workspace-") as workspace,
        ):
            store = SessionStore(Path(data))
            store.initialize()
            try:
                session = store.create_session(workspace)
                run, _ = store.create_run(session["id"], "large output")
                item = store.create_assistant_item(run["id"], 1)
                store.append_item_content(item["id"], "\n" * (512 * 1024))
                store.complete_assistant_and_run(item["id"], run["id"])
                snapshot = store.read_session_snapshot(session["id"])
            finally:
                store.close()

            self.assertGreaterEqual(len(snapshot["items"]), 1)
            assistant = snapshot["items"][-1]
            self.assertTrue(str(assistant["content"]).endswith("…[history truncated]"))
            self.assertLessEqual(
                len(json.dumps(snapshot, ensure_ascii=False).encode("utf-8")),
                1024 * 1024,
            )

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
