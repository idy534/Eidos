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

from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel  # noqa: E402
from eidos_runtime.context.builder import ContextBuilder  # noqa: E402
from eidos_runtime.extensions.skills import SkillCatalog  # noqa: E402
from eidos_runtime.sandbox.seatbelt import SeatbeltSelfTestResult  # noqa: E402
from eidos_runtime.sandbox.sensitive import SensitiveScanner  # noqa: E402
from eidos_runtime.protocol.server import (  # noqa: E402
    RuntimeServer,
    clean_session_title,
    valid_request_id,
)
from eidos_runtime.db.storage import SessionStore, WorkspaceBoundaryError  # noqa: E402


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


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _init_repository(repository: Path) -> Path:
    repository.mkdir(exist_ok=True)
    _git(repository, "init", "-q", "-b", "main")
    _git(repository, "config", "user.email", "eidos-tests@example.com")
    _git(repository, "config", "user.name", "Eidos Tests")
    (repository / "README.md").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-qm", "initial")
    return repository


def _repository(parent: Path) -> Path:
    return _init_repository(parent / "repository")


class RuntimeProtocolTests(unittest.TestCase):
    def test_generated_session_title_is_single_line_and_bounded(self) -> None:
        title = clean_session_title('  “分析\nCodex\u202e 架构”  ')
        long_title = clean_session_title("任务" * 100)

        self.assertEqual(title, "分析 Codex 架构")
        self.assertLessEqual(len(long_title), 60)
        self.assertLessEqual(len(long_title.encode("utf-8")), 120)

    def test_model_selection_and_session_mutations_use_closed_rpc_contracts(self) -> None:
        with (
            tempfile.TemporaryDirectory(prefix="eidos-data-") as data_directory,
            tempfile.TemporaryDirectory(prefix="eidos-workspace-") as workspace,
        ):
            output = io.StringIO()
            server = RuntimeServer(output, Path(data_directory), ScriptedModel([]))
            server.store.initialize()
            server.initialized = True
            server.sensitive = SensitiveScanner()
            server.supervisor.prepare_next = lambda: None  # type: ignore[method-assign]
            session = server.store.create_session(workspace)

            server.handle({
                "jsonrpc": "2.0", "id": "client-models",
                "method": "model/list", "params": {},
            })
            server.handle({
                "jsonrpc": "2.0", "id": "client-run",
                "method": "run/start",
                "params": {
                    "sessionId": session["id"], "userInput": "inspect",
                    "modelId": "deepseek-v4-pro",
                },
            })
            run = next(
                json.loads(line)["result"]
                for line in output.getvalue().splitlines()
                if json.loads(line).get("id") == "client-run"
            )
            server.store.cancel_run(run["id"])
            server.handle({
                "jsonrpc": "2.0", "id": "client-rename",
                "method": "session/rename",
                "params": {"sessionId": session["id"], "title": "新标题"},
            })
            server.handle({
                "jsonrpc": "2.0", "id": "client-delete",
                "method": "session/delete", "params": {"sessionId": session["id"]},
            })
            messages = {
                message["id"]: message
                for line in output.getvalue().splitlines()
                if (message := json.loads(line)).get("id", "").startswith("client-")
            }

            self.assertEqual(
                [model["id"] for model in messages["client-models"]["result"]["models"]],
                [],
            )
            self.assertEqual(run["modelId"], "deepseek-v4-pro")
            self.assertEqual(messages["client-rename"]["result"]["title"], "新标题")
            self.assertEqual(
                messages["client-delete"]["result"],
                {"deletedSessionId": session["id"]},
            )
            server.close()

    def test_waiting_approval_releases_execution_slot_and_requeues_fifo(self) -> None:
        with (
            tempfile.TemporaryDirectory(prefix="eidos-data-") as data_directory,
            tempfile.TemporaryDirectory(prefix="eidos-workspace-a-") as workspace_a,
            tempfile.TemporaryDirectory(prefix="eidos-workspace-b-") as workspace_b,
        ):
            output = io.StringIO()
            model = ScriptedModel([
                ModelResponse(tool_calls=(ModelToolCall(
                    "call-shell",
                    "run_shell",
                    {
                        "command": "printf approved-shell",
                        "sandboxPermissions": "with_additional_permissions",
                        "additionalPermissions": {"network": {"enabled": True}},
                        "justification": "Test execution-slot approval handling",
                    },
                ),)),
                ModelResponse(text="second completed"),
                ModelResponse(text="first continued"),
            ])
            server = RuntimeServer(output, Path(data_directory), model)
            with patch(
                "eidos_runtime.protocol.server.run_seatbelt_self_test",
                return_value=SeatbeltSelfTestResult(True, ("test",), ()),
            ):
                server.handle({
                    "jsonrpc": "2.0", "id": "client-init", "method": "initialize",
                    "params": {"client": {"name": "test", "version": "1"}, "protocolVersion": 1},
                })
            first_session = server.store.create_session(workspace_a)
            second_session = server.store.create_session(workspace_b)
            snapshot = SkillCatalog(server.plugins).extension_snapshot()
            first, _ = server.store.enqueue_run(
                first_session["id"], "write a file", extension_snapshot=snapshot
            )
            second, _ = server.store.enqueue_run(
                second_session["id"], "answer now", extension_snapshot=snapshot
            )
            server.supervisor.schedule_next()

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
                                "call-shell",
                                "run_shell",
                                {
                                    "command": "printf approved-shell",
                                    "sandboxPermissions": "with_additional_permissions",
                                    "additionalPermissions": {
                                        "network": {"enabled": True},
                                    },
                                    "justification": "Test approval channel failure",
                                },
                            ),
                        )
                    )
                ]
            )
            server = RuntimeServer(output, Path(data_directory), model)
            server.store.initialize()
            server.shell_available = True
            session = server.store.create_session(workspace)
            run, _ = server.store.create_run(session["id"], "create new.txt")

            def fail_approval(_params: object, _cancel: object) -> object:
                raise RuntimeError("approval channel failed")

            server.supervisor.request_approval = fail_approval  # type: ignore[method-assign]
            start_gate = threading.Event()
            start_gate.set()
            with self.assertLogs("eidos.runtime", level="ERROR"):
                server.supervisor._run_worker(
                    run["id"], threading.Event(), start_gate
                )

            failed = server.store.read_run(run["id"])
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["errorCode"], "INTERNAL_ERROR")
            replacement, _ = server.store.create_run(session["id"], "try again")
            self.assertEqual(replacement["status"], "running")
            server.store.cancel_run(replacement["id"])
            server.close()

    def test_shared_v1_vectors_match_runtime_envelopes(self) -> None:
        vectors = json.loads(PROTOCOL_V1_FIXTURE.read_text(encoding="utf-8"))
        output = io.StringIO()

        with tempfile.TemporaryDirectory(prefix="eidos-data-") as data_directory:
            server = RuntimeServer(output, Path(data_directory))
            with patch(
                "eidos_runtime.protocol.server.run_seatbelt_self_test",
                return_value=SeatbeltSelfTestResult(
                    available=False,
                    passed_checks=(),
                    failures=("fixture",),
                ),
            ):
                server.handle(vectors["initialize"]["request"])
            server.close()

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
                    "eidos_runtime.protocol.server.run_seatbelt_self_test",
                    return_value=SeatbeltSelfTestResult(
                        available=True,
                        passed_checks=("workspace_write",),
                        failures=(),
                    ),
                ) as self_test,
                self.assertLogs("eidos.runtime", level="INFO") as logs,
            ):
                server.handle(request)
            server.close()

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
        self.assertEqual(stdout_messages[0]["result"]["runtimeVersion"], "0.3.0")
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
            tempfile.TemporaryDirectory(prefix="eidos-workspace-") as workspace_parent,
        ):
            repository = _repository(Path(workspace_parent))
            first = run_runtime(
                [
                    initialize_message("client-1"),
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": "client-2",
                            "method": "session/create",
                            "params": {
                                "workspaceRoot": str(repository),
                                "executionMode": "worktree",
                            },
                        }
                    ),
                    shutdown_message("client-3"),
                ],
                Path(data_directory),
            )
            created = json.loads(first.stdout.splitlines()[1])["result"]

            inspection = SessionStore(Path(data_directory))
            inspection.initialize()
            try:
                assert inspection.connection is not None
                binding = inspection.connection.execute(
                    """
                    SELECT sessions.workspace_root, sessions.worktree_id,
                           worktrees.worktree_root
                    FROM sessions
                    JOIN worktrees ON worktrees.id = sessions.worktree_id
                    WHERE sessions.id = ?
                    """,
                    (created["id"],),
                ).fetchone()
                self.assertIsNotNone(binding)
                self.assertEqual(binding["workspace_root"], str(repository.resolve()))
                self.assertTrue(binding["worktree_id"])
                self.assertTrue(Path(binding["worktree_root"]).is_dir())
            finally:
                inspection.close()

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

            self.assertEqual(created["workspaceRoot"], str(repository.resolve()))
            self.assertEqual(responses[1]["result"], {"items": [created]})
            self.assertEqual(
                responses[2]["result"],
                {
                    "session": created,
                    "runs": [],
                    "items": [],
                    "stepResolutions": [],
                    "throughEventId": 1,
                },
            )
            self.assertEqual((Path(data_directory) / "state.sqlite").stat().st_mode & 0o777, 0o600)

    def test_session_rejects_workspace_containing_runtime_data(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-overlap-") as root_directory:
            root = Path(root_directory)
            _init_repository(root)
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

    def test_context_builder_reports_candidate_overflow_without_unbounded_load(self) -> None:
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

                current, _ = store.create_run(session["id"], "continue")
                built = ContextBuilder(store).build(current["id"])
                self.assertTrue(built.budget.fits)
                self.assertTrue(built.facts.candidate_overflow)
                self.assertLessEqual(len(built.facts.items), 200)
                self.assertIn(
                    "continue", [item.content for item in built.facts.items]
                )
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
            tempfile.TemporaryDirectory(prefix="eidos-workspace-") as workspace_parent,
        ):
            repository = _repository(Path(workspace_parent))
            messages = [initialize_message("client-1")]
            for index in range(3):
                messages.append(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": f"client-{index + 2}",
                            "method": "session/create",
                            "params": {"workspaceRoot": str(repository)},
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
            _init_repository(real_workspace)
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

    def test_local_model_configuration_is_private_and_loaded_after_restart(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-data-") as data_directory:
            configured = run_runtime(
                [
                    initialize_message("client-1"),
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": "client-2",
                            "method": "model/create",
                            "params": {
                                "provider": "deepseek",
                                "modelId": "deepseek-v4-flash",
                                "apiKey": "sk-example-key-for-tests",
                            },
                        }
                    ),
                    shutdown_message("client-3"),
                ],
                Path(data_directory),
            )
            configured_response = json.loads(configured.stdout.splitlines()[1])
            self.assertEqual(configured_response["result"]["id"], "deepseek-v4-flash")
            self.assertNotIn("apiKey", configured_response["result"])
            self.assertNotIn("sk-example", configured.stdout)
            self.assertTrue((Path(data_directory) / "models.json").is_file())
            self.assertFalse((Path(data_directory) / "model-secrets.json").exists())

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
