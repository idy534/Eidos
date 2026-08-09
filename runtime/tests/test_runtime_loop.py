from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch as mock_patch


import sys

RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel  # noqa: E402
from eidos_runtime.runtime.loop import ApprovalDecision, RuntimeLoop  # noqa: E402
from eidos_runtime.db.storage import ActiveRunError, SessionStore  # noqa: E402
from eidos_runtime.sandbox.seatbelt import (  # noqa: E402
    SeatbeltUnavailableError,
    is_seatbelt_ready,
)
from eidos_runtime.tools.workspace import (  # noqa: E402
    ToolCancelled,
    ToolExecutor,
    WorkspacePathError,
)


class RuntimeLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.seatbelt_ready = mock_patch(
            "eidos_runtime.runtime.tool_runtime.is_seatbelt_ready",
            return_value=True,
        )
        self.seatbelt_ready.start()
        self.addCleanup(self.seatbelt_ready.stop)
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="eidos-loop-")
        root = Path(self.temporary_directory.name)
        self.data_directory = root / "data"
        self.workspace = root / "workspace"
        self.data_directory.mkdir(mode=0o700)
        self.workspace.mkdir()
        (self.workspace / "hello.txt").write_text("hello from workspace\n", encoding="utf-8")
        self.store = SessionStore(self.data_directory)
        self.store.initialize()
        self.session = self.store.create_session(str(self.workspace))

    def tearDown(self) -> None:
        self.store.close()
        self.temporary_directory.cleanup()

    def test_fake_model_reads_a_real_file_then_completes_with_final_answer(self) -> None:
        run, _user_item = self.store.create_run(self.session["id"], "Read hello.txt")
        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        ModelToolCall("call-1", "read_file", {"path": "hello.txt"}),
                    )
                ),
                ModelResponse(text="The file says hello."),
            ]
        )
        notifications: list[dict[str, object]] = []

        RuntimeLoop(self.store, model, notifications.append).run(
            run["id"], threading.Event()
        )

        snapshot = self.store.read_session_snapshot(self.session["id"])
        self.assertEqual(snapshot["runs"][0]["status"], "succeeded")
        self.assertEqual(
            [item["kind"] for item in snapshot["items"]],
            ["user_message", "tool_call", "assistant_message"],
        )
        tool_item = snapshot["items"][1]
        self.assertEqual(tool_item["toolCall"]["toolName"], "read_file")
        result = json.loads(tool_item["toolCall"]["resultJson"])
        self.assertEqual(result["data"]["content"], "hello from workspace\n")
        self.assertEqual(model.contexts[1][-1]["type"], "tool_result")
        self.assertEqual(
            [notification["method"] for notification in notifications],
            [
                "run/started",
                "item/started",
                "item/completed",
                "item/started",
                "item/completed",
                "run/updated",
                "item/started",
                "item/delta",
                "item/completed",
                "run/completed",
            ],
        )

    def test_multiple_read_tools_execute_in_declared_order(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Inspect workspace")
        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        ModelToolCall("call-list", "list_files", {}),
                        ModelToolCall(
                            "call-read", "read_file", {"path": "hello.txt"}
                        ),
                    )
                ),
                ModelResponse(text="Inspection complete."),
            ]
        )

        RuntimeLoop(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )

        snapshot = self.store.read_session_snapshot(self.session["id"])
        tool_items = [item for item in snapshot["items"] if item.get("toolCall")]
        self.assertEqual(
            [item["toolCall"]["toolName"] for item in tool_items],
            ["list_files", "read_file"],
        )
        self.assertEqual(
            [entry["name"] for entry in model.contexts[1] if entry["type"] == "tool_result"],
            ["list_files", "read_file"],
        )

    def test_second_consecutive_empty_response_finalizes_the_run(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Invalid responses")
        model = ScriptedModel([ModelResponse(), ModelResponse()])

        RuntimeLoop(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )

        stopped = self.store.read_run(run["id"])
        self.assertEqual(stopped["status"], "stopped")
        self.assertEqual(stopped["stopReason"], "repeated_empty_response")
        self.assertEqual(len(model.contexts), 3)

    def test_repeated_tool_calls_finalize_the_run(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Keep reading")
        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        ModelToolCall(
                            f"call-{index}",
                            "read_file",
                            {"path": "hello.txt"},
                        ),
                    )
                )
                for index in range(20)
            ]
        )

        RuntimeLoop(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )

        stopped = self.store.read_run(run["id"])
        self.assertEqual(stopped["status"], "stopped")
        self.assertEqual(stopped["stopReason"], "repeated_tool_call")
        self.assertEqual(stopped["modelStepCount"], 3)
        self.assertEqual(len(model.contexts), 4)

    def test_second_active_run_is_rejected(self) -> None:
        self.store.create_run(self.session["id"], "First")

        with self.assertRaises(ActiveRunError):
            self.store.create_run(self.session["id"], "Second")

    def test_projection_safety_overflow_compacts_before_sampling(self) -> None:
        for index in range(13):
            historical, _ = self.store.create_run(
                self.session["id"], f"{index}:" + "x" * (64 * 1024 - 3)
            )
            self.store.fail_run(historical["id"], "TEST_COMPLETE")
        run, _ = self.store.create_run(self.session["id"], "Continue")
        model = ScriptedModel([ModelResponse(text="continued")])
        notifications: list[dict[str, object]] = []

        RuntimeLoop(self.store, model, notifications.append).run(
            run["id"], threading.Event()
        )

        completed = self.store.read_run(run["id"])
        self.assertEqual(completed["status"], "succeeded")
        self.assertGreaterEqual(self.store.compaction_count(run["id"]), 1)
        self.assertEqual(len(model.contexts), 1)

    def test_cancel_prevents_a_late_model_result_from_succeeding_the_run(self) -> None:
        run, _user_item = self.store.create_run(self.session["id"], "Wait")
        model = BlockingModel()
        notifications: list[dict[str, object]] = []
        cancellation = threading.Event()
        worker = threading.Thread(
            target=RuntimeLoop(self.store, model, notifications.append).run,
            args=(run["id"], cancellation),
        )
        worker.start()
        self.assertTrue(model.started.wait(timeout=2))

        cancellation.set()
        canceled = self.store.cancel_run(run["id"])
        model.release.set()
        worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertEqual(canceled["status"], "canceled")
        persisted = self.store.read_run(run["id"])
        self.assertEqual(persisted["status"], "canceled")
        self.assertFalse(any(item["kind"] == "assistant_message" for item in self.store.read_session_snapshot(self.session["id"])["items"]))

    def test_initialize_marks_an_abandoned_run_interrupted_without_replay(self) -> None:
        run, _user_item = self.store.create_run(self.session["id"], "Interrupted")
        self.store.close()

        self.store = SessionStore(self.data_directory)
        self.store.initialize()

        persisted = self.store.read_run(run["id"])
        self.assertEqual(persisted["status"], "interrupted")
        self.assertEqual(persisted["errorCode"], "RUNTIME_INTERRUPTED")

    def test_approved_new_file_is_atomically_written_then_model_continues(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Create notes.txt")
        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        ModelToolCall(
                            "call-write",
                            "write_file",
                            {"path": "notes.txt", "content": "approved\n"},
                        ),
                    )
                ),
                ModelResponse(text="Created notes.txt."),
            ]
        )
        approvals: list[dict[str, object]] = []

        notifications: list[dict[str, object]] = []
        RuntimeLoop(
            self.store,
            model,
            notifications.append,
            lambda request, _cancel: approvals.append(request)
            or ApprovalDecision("approve"),
        ).run(run["id"], threading.Event())

        self.assertEqual((self.workspace / "notes.txt").read_text(), "approved\n")
        self.assertEqual(len(approvals), 1)
        self.assertIn("+++ b/notes.txt", approvals[0]["diff"])
        snapshot = self.store.read_session_snapshot(self.session["id"])
        file_item = next(item for item in snapshot["items"] if item["kind"] == "file_change")
        self.assertEqual(file_item["status"], "completed")
        self.assertEqual(file_item["toolCall"]["approvalDecision"], "approve")
        self.assertEqual(
            json.loads(file_item["toolCall"]["argumentsJson"]),
            {"path": "notes.txt"},
        )
        completed_notification = next(
            notification
            for notification in notifications
            if notification["method"] == "item/completed"
            and notification["params"]["item"]["kind"] == "file_change"
        )
        completed_tool = completed_notification["params"]["item"]["toolCall"]
        self.assertNotIn("argumentsJson", completed_tool)
        self.assertNotIn("approvalDiff", completed_tool)

    def test_rejected_existing_file_change_has_zero_side_effects(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Change hello.txt")
        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        ModelToolCall("call-read", "read_file", {"path": "hello.txt"}),
                    )
                ),
                ModelResponse(
                    tool_calls=(
                        ModelToolCall(
                            "call-write",
                            "write_file",
                            {"path": "hello.txt", "content": "changed\n"},
                        ),
                    )
                ),
                ModelResponse(text="The change was declined."),
            ]
        )

        RuntimeLoop(
            self.store,
            model,
            lambda _message: None,
            lambda _request, _cancel: ApprovalDecision("reject"),
        ).run(run["id"], threading.Event())

        self.assertEqual(
            (self.workspace / "hello.txt").read_text(), "hello from workspace\n"
        )
        snapshot = self.store.read_session_snapshot(self.session["id"])
        file_item = next(item for item in snapshot["items"] if item["kind"] == "file_change")
        self.assertEqual(file_item["status"], "declined")
        rejection = next(
            item for item in model.contexts[-1]
            if item.get("type") == "tool_result"
            and item.get("name") == "write_file"
        )
        self.assertIn(
            "Do not request another approval",
            json.loads(str(rejection["result"]))["summary"],
        )

    def test_approval_version_conflict_preserves_external_change(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Change hello.txt")
        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        ModelToolCall("call-read", "read_file", {"path": "hello.txt"}),
                    )
                ),
                ModelResponse(
                    tool_calls=(
                        ModelToolCall(
                            "call-write",
                            "write_file",
                            {"path": "hello.txt", "content": "model change\n"},
                        ),
                    )
                ),
                ModelResponse(text="The file changed while waiting."),
            ]
        )

        def mutate_then_approve(_request, _cancel):
            (self.workspace / "hello.txt").write_text("external change\n")
            return ApprovalDecision("approve")

        RuntimeLoop(
            self.store,
            model,
            lambda _message: None,
            mutate_then_approve,
        ).run(run["id"], threading.Event())

        self.assertEqual((self.workspace / "hello.txt").read_text(), "external change\n")
        snapshot = self.store.read_session_snapshot(self.session["id"])
        file_item = next(item for item in snapshot["items"] if item["kind"] == "file_change")
        result = json.loads(file_item["toolCall"]["resultJson"])
        self.assertEqual(result["code"], "file_version_conflict")

    def test_existing_file_write_without_read_evidence_never_requests_approval(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Blind write")
        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        ModelToolCall(
                            "call-write",
                            "write_file",
                            {"path": "hello.txt", "content": "blind\n"},
                        ),
                    )
                ),
                ModelResponse(text="I need to read it first."),
            ]
        )
        approvals: list[object] = []

        RuntimeLoop(
            self.store,
            model,
            lambda _message: None,
            lambda request, _cancel: approvals.append(request) or ApprovalDecision("approve"),
        ).run(run["id"], threading.Event())

        self.assertEqual(approvals, [])
        self.assertEqual(
            (self.workspace / "hello.txt").read_text(), "hello from workspace\n"
        )

    def test_apply_patch_without_read_evidence_never_requests_approval(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Blind Patch")
        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        ModelToolCall(
                            "call-patch",
                            "apply_patch",
                            {
                                "path": "hello.txt",
                                "patch": "--- a/hello.txt\n+++ b/hello.txt\n"
                                "@@ -1 +1 @@\n-hello from workspace\n+blind\n",
                            },
                        ),
                    )
                ),
                ModelResponse(text="I need to read it first."),
            ]
        )
        approvals: list[object] = []

        RuntimeLoop(
            self.store,
            model,
            lambda _message: None,
            lambda request, _cancel: approvals.append(request) or ApprovalDecision("approve"),
        ).run(run["id"], threading.Event())

        self.assertEqual(approvals, [])
        self.assertEqual(
            (self.workspace / "hello.txt").read_text(), "hello from workspace\n"
        )

    def test_invalid_or_mismatched_patch_never_requests_approval(self) -> None:
        patches = (
            "--- a/hello.txt\n+++ b/hello.txt\n",
            "--- a/hello.txt\n+++ b/hello.txt\n@@ -1 +1 @@\n-other\n+new\n",
        )
        for index, patch_text in enumerate(patches):
            with self.subTest(patch_text=patch_text):
                run, _ = self.store.create_run(self.session["id"], "Invalid Patch")
                model = ScriptedModel(
                    [
                        ModelResponse(
                            tool_calls=(
                                ModelToolCall(
                                    f"call-read-{index}", "read_file", {"path": "hello.txt"}
                                ),
                            )
                        ),
                        ModelResponse(
                            tool_calls=(
                                ModelToolCall(
                                    f"call-patch-{index}",
                                    "apply_patch",
                                    {"path": "hello.txt", "patch": patch_text},
                                ),
                            )
                        ),
                        ModelResponse(text="The Patch could not be applied."),
                    ]
                )
                approvals: list[object] = []

                RuntimeLoop(
                    self.store,
                    model,
                    lambda _message: None,
                    lambda request, _cancel: approvals.append(request)
                    or ApprovalDecision("approve"),
                ).run(run["id"], threading.Event())

                self.assertEqual(approvals, [])
                snapshot = self.store.read_session_snapshot(self.session["id"])
                file_item = [
                    item for item in snapshot["items"] if item["kind"] == "file_change"
                ][-1]
                result = json.loads(file_item["toolCall"]["resultJson"])
                expected = "invalid_patch" if index == 0 else "patch_context_mismatch"
                self.assertEqual(result["code"], expected)
                self.assertEqual(
                    (self.workspace / "hello.txt").read_text(),
                    "hello from workspace\n",
                )

    def test_approved_shell_runs_in_sandbox_and_returns_output(self) -> None:
        if not is_seatbelt_ready():
            self.skipTest(
                "Seatbelt Shell integration requires a currently usable sandbox-exec and static resources"
            )
        run, _ = self.store.create_run(self.session["id"], "Run a command")
        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        ModelToolCall(
                            "call-shell",
                            "run_shell",
                            {"command": "printf shell-ok", "timeoutSeconds": 5},
                        ),
                    )
                ),
                ModelResponse(text="Command completed."),
            ]
        )

        RuntimeLoop(
            self.store,
            model,
            lambda _message: None,
            lambda _request, _cancel: ApprovalDecision("approve"),
            shell_available=True,
        ).run(run["id"], threading.Event())

        snapshot = self.store.read_session_snapshot(self.session["id"])
        command_item = next(
            item for item in snapshot["items"] if item["kind"] == "command_execution"
        )
        self.assertEqual(
            json.loads(command_item["toolCall"]["argumentsJson"]),
            {"command": "printf shell-ok", "cwd": ".", "timeoutSeconds": 5},
        )
        result = json.loads(command_item["toolCall"]["resultJson"])
        self.assertEqual(result["outcome"], "success")
        self.assertEqual(result["data"]["stdout"], "shell-ok")
        self.assertEqual(result["data"]["stderr"], "")

    def test_successful_first_shell_with_incomplete_manifest_continues_run(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Inspect a large repo")
        command = 'ls . && echo "---" && ls codex-rs 2>/dev/null'
        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        ModelToolCall(
                            "call-shell",
                            "run_shell",
                            {"command": command, "timeoutSeconds": 5},
                        ),
                    )
                ),
                ModelResponse(text="Inspection continued after Shell."),
            ]
        )
        shell_result = {
            "schemaVersion": 1,
            "toolName": "run_shell",
            "outcome": "success",
            "code": "ok",
            "summary": "Command completed",
            "data": {
                "exitCode": 0,
                "stdout": "hello.txt\n---\n",
                "stderr": "",
                "truncated": False,
                "termination": "exit",
                "durationMs": 1,
            },
            "sideEffectsMayExist": True,
        }

        with (
            mock_patch(
                "eidos_runtime.runtime.tool_runtime.run_shell",
                return_value=shell_result,
            ),
            mock_patch.object(
                ToolExecutor,
                "refresh_workspace_index",
                side_effect=WorkspacePathError("WORKSPACE_INDEX_INCOMPLETE"),
            ),
        ):
            RuntimeLoop(
                self.store,
                model,
                lambda _message: None,
                lambda _request, _cancel: ApprovalDecision("approve"),
                shell_available=True,
            ).run(run["id"], threading.Event())

        snapshot = self.store.read_session_snapshot(self.session["id"])
        command_item = next(
            item for item in snapshot["items"] if item["kind"] == "command_execution"
        )
        result = json.loads(command_item["toolCall"]["resultJson"])
        self.assertEqual(command_item["toolCall"]["status"], "completed")
        self.assertEqual(result["outcome"], "success")
        self.assertEqual(result["code"], "ok")
        self.assertEqual(result["data"]["exitCode"], 0)
        self.assertEqual(result["data"]["termination"], "exit")
        self.assertEqual(result["data"]["workspaceChangeState"], "unknown")
        self.assertTrue(result["data"]["workspaceDiffIncomplete"])
        self.assertFalse(result["reconciliationRequired"])
        self.assertEqual(len(model.contexts), 2)
        persisted_run = self.store.read_run(run["id"])
        self.assertEqual(persisted_run["status"], "succeeded")
        self.assertEqual(
            tuple(self.store.connection.execute(
                """
                SELECT reconciliation_required, side_effects_may_exist
                FROM runs WHERE id = ?
                """,
                (run["id"],),
            ).fetchone()),
            (0, 0),
        )
        self.assertEqual(
            self.store.connection.execute(
                "SELECT status FROM durable_intents WHERE run_id = ?",
                (run["id"],),
            ).fetchone()[0],
            "completed",
        )
        self.assertEqual(
            self.store.connection.execute(
                """
                SELECT COUNT(*) FROM events
                WHERE run_id = ? AND event_type = 'reconciliation.required'
                """,
                (run["id"],),
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.store.connection.execute(
                "SELECT COUNT(*) FROM steps WHERE run_id = ? AND status = 'completed'",
                (run["id"],),
            ).fetchone()[0],
            2,
        )

    def test_unavailable_shell_fails_before_approval_or_execution(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Run a command")
        sentinel = self.workspace / "must-not-exist"
        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        ModelToolCall(
                            "call-shell",
                            "run_shell",
                            {
                                "command": f"/usr/bin/touch {sentinel}",
                                "timeoutSeconds": 5,
                            },
                        ),
                    )
                ),
                ModelResponse(text="Shell is unavailable."),
            ]
        )
        approvals: list[object] = []

        RuntimeLoop(
            self.store,
            model,
            lambda _message: None,
            lambda request, _cancel: approvals.append(request)
            or ApprovalDecision("approve"),
            shell_available=False,
        ).run(run["id"], threading.Event())

        snapshot = self.store.read_session_snapshot(self.session["id"])
        command_item = next(
            item for item in snapshot["items"] if item["kind"] == "command_execution"
        )
        result = json.loads(command_item["toolCall"]["resultJson"])
        self.assertEqual(result["code"], "sandbox_unavailable")
        self.assertFalse(result["sideEffectsMayExist"])
        self.assertEqual(approvals, [])
        self.assertFalse(sentinel.exists())

    def test_dynamic_seatbelt_unavailable_before_approval_starts_nothing(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Run a command")
        sentinel = self.workspace / "must-not-exist"
        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        ModelToolCall(
                            "call-shell",
                            "run_shell",
                            {
                                "command": f"/usr/bin/touch {sentinel}",
                                "timeoutSeconds": 5,
                            },
                        ),
                    )
                ),
                ModelResponse(text="Shell is unavailable."),
            ]
        )
        approvals: list[object] = []

        with (
            mock_patch(
                "eidos_runtime.runtime.tool_runtime.is_seatbelt_ready",
                return_value=False,
            ),
            mock_patch(
                "eidos_runtime.sandbox.shell.subprocess.Popen",
                side_effect=AssertionError("process must not start"),
            ),
        ):
            RuntimeLoop(
                self.store,
                model,
                lambda _message: None,
                lambda request, _cancel: approvals.append(request)
                or ApprovalDecision("approve"),
                shell_available=True,
            ).run(run["id"], threading.Event())

        snapshot = self.store.read_session_snapshot(self.session["id"])
        command_item = next(
            item for item in snapshot["items"] if item["kind"] == "command_execution"
        )
        result = json.loads(command_item["toolCall"]["resultJson"])
        self.assertEqual(result["code"], "sandbox_unavailable")
        self.assertFalse(result["sideEffectsMayExist"])
        self.assertFalse(result["reconciliationRequired"])
        self.assertEqual(result["data"]["termination"], "not_started")
        self.assertEqual(approvals, [])
        self.assertEqual(
            self.store.connection.execute(
                "SELECT COUNT(*) FROM durable_intents WHERE run_id = ?",
                (run["id"],),
            ).fetchone()[0],
            0,
        )
        self.assertFalse(sentinel.exists())

    def test_dynamic_seatbelt_unavailable_after_approval_never_spawns(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Run a command")
        sentinel = self.workspace / "must-not-exist"
        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        ModelToolCall(
                            "call-shell",
                            "run_shell",
                            {
                                "command": f"/usr/bin/touch {sentinel}",
                                "timeoutSeconds": 5,
                            },
                        ),
                    )
                ),
                ModelResponse(text="Shell is unavailable."),
            ]
        )
        approvals: list[object] = []

        with (
            mock_patch(
                "eidos_runtime.runtime.tool_runtime.is_seatbelt_ready",
                return_value=True,
            ),
            mock_patch(
                "eidos_runtime.sandbox.seatbelt.SeatbeltProfile.command",
                side_effect=SeatbeltUnavailableError(
                    "internal seatbelt diagnostic must stay private"
                ),
            ),
            mock_patch(
                "eidos_runtime.sandbox.shell.subprocess.Popen",
                side_effect=AssertionError("process must not start"),
            ) as popen,
        ):
            RuntimeLoop(
                self.store,
                model,
                lambda _message: None,
                lambda request, _cancel: approvals.append(request)
                or ApprovalDecision("approve"),
                shell_available=True,
            ).run(run["id"], threading.Event())

        snapshot = self.store.read_session_snapshot(self.session["id"])
        command_item = next(
            item for item in snapshot["items"] if item["kind"] == "command_execution"
        )
        result_json = command_item["toolCall"]["resultJson"]
        result = json.loads(result_json)
        self.assertEqual(len(approvals), 1)
        self.assertEqual(result["code"], "sandbox_unavailable")
        self.assertFalse(result["sideEffectsMayExist"])
        self.assertFalse(result["reconciliationRequired"])
        self.assertEqual(result["data"]["termination"], "not_started")
        self.assertNotIn("internal seatbelt diagnostic", result_json)
        self.assertNotEqual(result["code"], "TOOL_EXECUTION_FAILED")
        self.assertFalse(self.store.side_effects_blocked(run["id"]))
        self.assertEqual(
            self.store.connection.execute(
                "SELECT status FROM durable_intents WHERE run_id = ?",
                (run["id"],),
            ).fetchone()[0],
            "completed",
        )
        self.assertFalse(sentinel.exists())
        popen.assert_not_called()

    def test_rejected_shell_has_zero_side_effects(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Reject a command")
        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        ModelToolCall(
                            "call-shell",
                            "run_shell",
                            {"command": "touch rejected.txt"},
                        ),
                    )
                ),
                ModelResponse(text="Command rejected."),
            ]
        )

        RuntimeLoop(
            self.store,
            model,
            lambda _message: None,
            lambda _request, _cancel: ApprovalDecision("reject"),
            shell_available=True,
        ).run(run["id"], threading.Event())

        self.assertFalse((self.workspace / "rejected.txt").exists())

    def test_shell_workspace_rebind_after_approval_never_runs_in_replacement(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Run safely")
        replacement = self.workspace.parent / "replacement"
        replacement.mkdir()
        moved = self.workspace.parent / "moved-workspace"
        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        ModelToolCall(
                            "call-shell",
                            "run_shell",
                            {"command": "touch escaped.txt", "timeoutSeconds": 5},
                        ),
                    )
                ),
                ModelResponse(text="Command was blocked."),
            ]
        )

        def rebind_then_approve(_request, _cancel):
            self.workspace.rename(moved)
            replacement.rename(self.workspace)
            return ApprovalDecision("approve")

        RuntimeLoop(
            self.store,
            model,
            lambda _message: None,
            rebind_then_approve,
            shell_available=True,
        ).run(run["id"], threading.Event())

        self.assertFalse((self.workspace / "escaped.txt").exists())
        snapshot = self.store.read_session_snapshot(self.session["id"])
        command_item = next(
            item for item in snapshot["items"] if item["kind"] == "command_execution"
        )
        result = json.loads(command_item["toolCall"]["resultJson"])
        self.assertEqual(result["code"], "workspace_identity_changed")

    def test_unrelated_sensitive_workspace_file_does_not_block_shell_approval(self) -> None:
        (self.workspace / "private.pem").write_text("secret", encoding="utf-8")
        run, _ = self.store.create_run(self.session["id"], "Read secrets")
        approvals: list[dict[str, object]] = []
        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        ModelToolCall(
                            "call-shell",
                            "run_shell",
                            {"command": "cat private.pem", "timeoutSeconds": 5},
                        ),
                    )
                ),
                ModelResponse(text="Command was blocked."),
            ]
        )

        RuntimeLoop(
            self.store,
            model,
            lambda _message: None,
            lambda request, _cancel: approvals.append(request)
            or ApprovalDecision("approve"),
            shell_available=True,
        ).run(run["id"], threading.Event())

        self.assertEqual(len(approvals), 1)
        snapshot = self.store.read_session_snapshot(self.session["id"])
        command_item = next(
            item for item in snapshot["items"] if item["kind"] == "command_execution"
        )
        result = json.loads(command_item["toolCall"]["resultJson"])
        self.assertNotEqual(result["code"], "sensitive_workspace_content")
        self.assertEqual(result["outcome"], "success")
        self.assertEqual(result["data"]["workspaceChangeState"], "unknown")
        self.assertTrue(result["data"]["workspaceDiffIncomplete"])
        self.assertFalse(result["reconciliationRequired"])
        self.assertEqual(len(model.contexts), 2)
        self.assertEqual(self.store.read_run(run["id"])["status"], "succeeded")

    def test_shell_write_with_hard_link_reaches_approval_and_reconciliation(self) -> None:
        external = self.workspace.parent / "external.txt"
        external.write_text("outside\n", encoding="utf-8")
        os.link(external, self.workspace / "linked.txt")
        run, _ = self.store.create_run(self.session["id"], "Modify linked file")
        approvals: list[dict[str, object]] = []
        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        ModelToolCall(
                            "call-shell",
                            "run_shell",
                            {"command": "printf changed > linked.txt", "timeoutSeconds": 5},
                        ),
                    )
                ),
                ModelResponse(text="Command was blocked."),
            ]
        )

        RuntimeLoop(
            self.store,
            model,
            lambda _message: None,
            lambda request, _cancel: approvals.append(request)
            or ApprovalDecision("approve"),
            shell_available=True,
        ).run(run["id"], threading.Event())

        self.assertEqual(len(approvals), 1)
        snapshot = self.store.read_session_snapshot(self.session["id"])
        command_item = next(
            item for item in snapshot["items"] if item["kind"] == "command_execution"
        )
        result = json.loads(command_item["toolCall"]["resultJson"])
        self.assertTrue(result["reconciliationRequired"])

    def test_read_only_shell_does_not_block_on_unrelated_hard_link(self) -> None:
        external = self.workspace.parent / "external.txt"
        external.write_text("outside\n", encoding="utf-8")
        os.link(external, self.workspace / "linked.txt")
        run, _ = self.store.create_run(self.session["id"], "Inspect workspace")
        approvals: list[dict[str, object]] = []
        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        ModelToolCall(
                            "call-shell",
                            "run_shell",
                            {"command": "ls -la", "timeoutSeconds": 5},
                        ),
                    )
                ),
                ModelResponse(text="Workspace inspected."),
            ]
        )

        RuntimeLoop(
            self.store,
            model,
            lambda _message: None,
            lambda request, _cancel: approvals.append(request)
            or ApprovalDecision("approve"),
            shell_available=True,
        ).run(run["id"], threading.Event())

        self.assertEqual(len(approvals), 1)
        self.assertEqual(external.read_text(encoding="utf-8"), "outside\n")
        snapshot = self.store.read_session_snapshot(self.session["id"])
        command_item = next(
            item for item in snapshot["items"] if item["kind"] == "command_execution"
        )
        result = json.loads(command_item["toolCall"]["resultJson"])
        self.assertNotEqual(result["code"], "unsupported_workspace_hardlink")

    def test_cancel_during_shell_preflight_cancels_run_without_internal_error(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Run a command")
        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        ModelToolCall(
                            "call-shell", "run_shell", {"command": "printf safe"}
                        ),
                    )
                )
            ]
        )

        with mock_patch(
            "eidos_runtime.runtime.run_resources.ToolExecutor.prepare_shell",
            side_effect=ToolCancelled(),
        ):
            RuntimeLoop(
                self.store,
                model,
                lambda _message: None,
                lambda _request, _cancel: ApprovalDecision("approve"),
                shell_available=True,
            ).run(run["id"], threading.Event())

        completed = self.store.read_run(run["id"])
        self.assertEqual(completed["status"], "canceled")
        self.assertNotEqual(completed.get("errorCode"), "INTERNAL_ERROR")

    def test_cancel_during_post_approval_shell_recheck_cancels_run(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Run a command")
        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        ModelToolCall(
                            "call-shell", "run_shell", {"command": "printf safe"}
                        ),
                    )
                )
            ]
        )
        original_prepare = ToolExecutor.prepare_shell
        calls = 0

        def cancel_on_second_scan(executor, value, cancel):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ToolCancelled
            return original_prepare(executor, value, cancel)

        with mock_patch(
            "eidos_runtime.runtime.run_resources.ToolExecutor.prepare_shell",
            side_effect=cancel_on_second_scan,
            autospec=True,
        ):
            RuntimeLoop(
                self.store,
                model,
                lambda _message: None,
                lambda _request, _cancel: ApprovalDecision("approve"),
                shell_available=True,
            ).run(run["id"], threading.Event())

        completed = self.store.read_run(run["id"])
        self.assertEqual(calls, 2)
        self.assertEqual(completed["status"], "canceled")


class ToolExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="eidos-tools-")
        self.workspace = Path(self.temporary_directory.name)
        (self.workspace / "src").mkdir()
        (self.workspace / "src" / "app.py").write_text(
            "print('needle')\n", encoding="utf-8"
        )
        (self.workspace / ".env").write_text("SECRET=value\n", encoding="utf-8")
        self.executor = ToolExecutor(self.workspace)

    def tearDown(self) -> None:
        self.executor.close()
        self.temporary_directory.cleanup()

    def test_list_read_and_literal_search_are_bounded_to_workspace(self) -> None:
        listed = self.executor.execute("list_files", {}, threading.Event())
        read = self.executor.execute(
            "read_file", {"path": "src/app.py"}, threading.Event()
        )
        searched = self.executor.execute(
            "search_text", {"query": "needle"}, threading.Event()
        )

        self.assertEqual(listed["outcome"], "success")
        self.assertIn("src/app.py", listed["data"]["paths"])
        self.assertEqual(read["data"]["content"], "print('needle')\n")
        self.assertEqual(searched["data"]["matches"][0]["path"], "src/app.py")

    def test_unavailable_seatbelt_leaves_new_and_existing_targets_unchanged(self) -> None:
        existing = self.workspace / "existing.txt"
        existing.write_text("base\n", encoding="utf-8")

        with mock_patch(
            "eidos_runtime.sandbox.seatbelt.is_seatbelt_usable",
            return_value=False,
        ):
            for path, content in (
                ("new.txt", "new\n"),
                ("existing.txt", "changed\n"),
            ):
                with self.subTest(path=path):
                    prepared = self.executor.prepare_file_change(
                        "write_file",
                        {"path": path, "content": content},
                        threading.Event(),
                    )
                    assert not isinstance(prepared, dict)
                    result = self.executor.commit_file_change(
                        "write_file", prepared, threading.Event()
                    )

                    self.assertEqual(result["code"], "sandbox_unavailable")
                    self.assertFalse(result["sideEffectsMayExist"])
                    self.assertEqual(list(self.workspace.glob(".eidos-*.tmp")), [])

        self.assertFalse((self.workspace / "new.txt").exists())
        self.assertEqual(existing.read_text(encoding="utf-8"), "base\n")

    def test_sensitive_and_escaping_paths_are_rejected(self) -> None:
        sensitive = self.executor.execute(
            "read_file", {"path": ".env"}, threading.Event()
        )
        escaping = self.executor.execute(
            "read_file", {"path": "../outside"}, threading.Event()
        )

        self.assertEqual(sensitive["code"], "sensitive_path")
        self.assertEqual(escaping["code"], "invalid_arguments")

    def test_sensitive_file_content_is_withheld_as_a_whole(self) -> None:
        (self.workspace / "notes.txt").write_text(
            "public line\npassword=hunter2\n", encoding="utf-8"
        )
        result = self.executor.execute(
            "read_file", {"path": "notes.txt"}, threading.Event()
        )
        self.assertEqual(result["code"], "sensitive_content_rejected")
        self.assertNotIn("hunter2", json.dumps(result))
        self.assertNotIn("public line", json.dumps(result))

    def test_read_file_size_tiers_and_line_ranges_are_bounded(self) -> None:
        medium = self.workspace / "medium.txt"
        medium.write_text("a" * (300 * 1024), encoding="utf-8")
        huge = self.workspace / "huge.txt"
        huge.write_text("b" * (2 * 1024 * 1024 + 1), encoding="utf-8")
        lines = self.workspace / "lines.txt"
        lines.write_text("".join(f"line {number}\n" for number in range(1, 21)), encoding="utf-8")

        truncated = self.executor.execute(
            "read_file", {"path": "medium.txt"}, threading.Event()
        )
        rejected = self.executor.execute(
            "read_file", {"path": "huge.txt"}, threading.Event()
        )
        ranged = self.executor.execute(
            "read_file_range",
            {"path": "lines.txt", "startLine": 3, "endLine": 5},
            threading.Event(),
        )

        self.assertTrue(truncated["data"]["truncated"])
        self.assertEqual(truncated["data"]["truncationReason"], "head_tail")
        self.assertEqual(rejected["code"], "file_too_large")
        self.assertEqual(ranged["data"]["content"], "line 3\nline 4\nline 5\n")
        self.assertEqual(ranged["data"]["nextLine"], 6)

    def test_read_file_classifies_binary_and_non_utf8_content(self) -> None:
        (self.workspace / "binary.dat").write_bytes(b"text\x00binary")
        (self.workspace / "latin1.txt").write_bytes(b"caf\xe9")
        binary = self.executor.execute(
            "read_file", {"path": "binary.dat"}, threading.Event()
        )
        encoded = self.executor.execute(
            "read_file", {"path": "latin1.txt"}, threading.Event()
        )
        self.assertEqual(binary["code"], "binary_file")
        self.assertEqual(encoded["code"], "invalid_utf8")

    def test_search_is_ascii_case_insensitive_and_rejects_multiline_query(self) -> None:
        (self.workspace / "case.txt").write_text("Alpha NEEDLE omega\n", encoding="utf-8")
        result = self.executor.execute(
            "search_text", {"query": "needle"}, threading.Event()
        )
        invalid = self.executor.execute(
            "search_text", {"query": "one\ntwo"}, threading.Event()
        )

        self.assertEqual(result["data"]["matches"][0]["column"], 7)
        self.assertEqual(invalid["code"], "invalid_arguments")

    def test_delete_file_prepares_full_diff_and_commits_one_file(self) -> None:
        target = self.workspace / "delete-me.txt"
        target.write_text("remove me\n", encoding="utf-8")
        prepared = self.executor.prepare_file_change(
            "delete_file", {"path": "delete-me.txt"}, threading.Event()
        )
        self.assertFalse(isinstance(prepared, dict), prepared)
        assert not isinstance(prepared, dict)
        self.assertTrue(prepared.delete)
        self.assertIn("+++ /dev/null", prepared.diff)

        result = self.executor.commit_file_change(
            "delete_file", prepared, threading.Event()
        )

        self.assertEqual(result["outcome"], "success")
        self.assertFalse(target.exists())

    def test_all_results_use_the_versioned_canonical_contract(self) -> None:
        result = self.executor.execute("list_files", {}, threading.Event())
        self.assertEqual(result["schemaVersion"], 1)
        self.assertEqual(result["toolContractVersion"], 1)
        self.assertEqual(result["reconciliationRequired"], False)

    def test_read_and_search_never_expose_an_external_hard_link(self) -> None:
        outside = self.workspace.parent / f"{self.workspace.name}-outside-secret.txt"
        outside.write_text("external-hardlink-secret\n", encoding="utf-8")
        os.link(outside, self.workspace / "notes.txt")
        try:
            read = self.executor.execute(
                "read_file", {"path": "notes.txt"}, threading.Event()
            )
            searched = self.executor.execute(
                "search_text", {"query": "external-hardlink-secret"}, threading.Event()
            )
        finally:
            outside.unlink(missing_ok=True)

        self.assertEqual(read["outcome"], "error")
        self.assertEqual(read["code"], "unsupported_file_hardlink")
        self.assertEqual(searched["outcome"], "success")
        self.assertEqual(searched["data"]["matches"], [])
        self.assertNotIn("external-hardlink-secret", json.dumps(searched))

    def test_shell_preflight_allows_dependency_source_and_public_certificates(self) -> None:
        source = self.workspace / ".venv" / "lib" / "package"
        source.mkdir(parents=True)
        (source / "token.py").write_text("TOKEN_KIND = 'name'\n", encoding="utf-8")
        (source / "cacert.pem").write_text(
            "-----BEGIN CERTIFICATE-----\npublic-ca\n-----END CERTIFICATE-----\n",
            encoding="utf-8",
        )
        javascript = self.workspace / "node_modules" / "package"
        javascript.mkdir(parents=True)
        (javascript / "credentials.js").write_text(
            "export const credentialType = 'fixture';\n", encoding="utf-8"
        )

        identity = self.executor.prepare_shell(".", threading.Event())

        self.assertEqual(identity.path, self.workspace.resolve())

    def test_replacing_workspace_path_cannot_rebind_an_existing_executor(self) -> None:
        original = self.workspace / "original"
        outside = self.workspace / "outside"
        original.mkdir()
        outside.mkdir()
        (outside / "outside.txt").write_text("outside", encoding="utf-8")
        executor = ToolExecutor(original)
        try:
            original.rename(self.workspace / "moved")
            original.symlink_to(outside, target_is_directory=True)

            result = executor.execute(
                "read_file", {"path": "outside.txt"}, threading.Event()
            )

            self.assertEqual(result["code"], "workspace_identity_changed")
        finally:
            executor.close()

    def test_strict_unified_patch_prepares_and_commits_expected_content(self) -> None:
        patch = """--- a/src/app.py
+++ b/src/app.py
@@ -1 +1 @@
-print('needle')
+print('updated')
"""
        prepared = self.executor.prepare_file_change(
            "apply_patch",
            {"path": "src/app.py", "patch": patch},
            threading.Event(),
        )
        self.assertFalse(isinstance(prepared, dict), prepared)
        assert not isinstance(prepared, dict)

        result = self.executor.commit_file_change(
            "apply_patch", prepared, threading.Event()
        )

        self.assertEqual(result["outcome"], "success")
        self.assertEqual(
            (self.workspace / "src" / "app.py").read_text(), "print('updated')\n"
        )

    def test_existing_symlink_is_not_treated_as_a_new_file(self) -> None:
        outside = self.workspace / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")
        (self.workspace / "link.txt").symlink_to(outside)

        prepared = self.executor.prepare_file_change(
            "write_file",
            {"path": "link.txt", "content": "replacement\n"},
            threading.Event(),
        )

        self.assertIsInstance(prepared, dict)
        assert isinstance(prepared, dict)
        self.assertEqual(prepared["code"], "unsupported_file_type")
        self.assertTrue((self.workspace / "link.txt").is_symlink())
        self.assertEqual(outside.read_text(), "outside\n")

    def test_parent_moved_outside_workspace_during_secure_commit_is_not_written(self) -> None:
        subdirectory = self.workspace / "subdir"
        subdirectory.mkdir(exist_ok=True)
        target = subdirectory / "target.txt"
        target.write_text("base\n", encoding="utf-8")
        prepared = self.executor.prepare_file_change(
            "write_file",
            {"path": "subdir/target.txt", "content": "candidate\n"},
            threading.Event(),
        )
        assert not isinstance(prepared, dict)
        moved = self.workspace.parent / f"moved-{self.workspace.name}"

        def move_parent_then_attempt(workspace, source, destination, expected):
            subdirectory.rename(moved)
            from eidos_runtime.sandbox.seatbelt import secure_workspace_move

            return secure_workspace_move(workspace, source, destination, expected)

        try:
            with mock_patch(
                "eidos_runtime.tools.workspace.secure_workspace_move",
                side_effect=move_parent_then_attempt,
            ):
                result = self.executor.commit_file_change(
                    "write_file", prepared, threading.Event()
                )
            self.assertEqual(result["outcome"], "error")
            self.assertFalse(result["sideEffectsMayExist"])
            self.assertEqual((moved / "target.txt").read_text(), "base\n")
        finally:
            if moved.exists():
                moved.rename(subdirectory)

    def test_cancel_after_atomic_move_reports_committed_result(self) -> None:
        prepared = self.executor.prepare_file_change(
            "write_file",
            {"path": "committed.txt", "content": "committed\n"},
            threading.Event(),
        )
        assert not isinstance(prepared, dict)
        cancel = threading.Event()

        from eidos_runtime.sandbox.seatbelt import secure_workspace_move

        def move_then_cancel(workspace, source, destination, expected):
            moved = secure_workspace_move(workspace, source, destination, expected)
            cancel.set()
            return moved

        with mock_patch(
            "eidos_runtime.tools.workspace.secure_workspace_move",
            side_effect=move_then_cancel,
        ):
            result = self.executor.commit_file_change(
                "write_file", prepared, cancel
            )

        self.assertEqual(result["outcome"], "success")
        self.assertEqual((self.workspace / "committed.txt").read_text(), "committed\n")

    def test_control_characters_in_paths_are_rejected(self) -> None:
        result = self.executor.prepare_file_change(
            "write_file",
            {"path": "safe.txt\n+++ b/forged", "content": "content"},
            threading.Event(),
        )

        self.assertIsInstance(result, dict)
        assert isinstance(result, dict)
        self.assertEqual(result["code"], "workspace_boundary_violation")

    def test_post_commit_fsync_failure_reports_possible_side_effect(self) -> None:
        prepared = self.executor.prepare_file_change(
            "write_file",
            {"path": "durability.txt", "content": "committed\n"},
            threading.Event(),
        )
        assert not isinstance(prepared, dict)
        real_fsync = os.fsync
        calls = 0

        def fail_parent_fsync(descriptor):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("fixture fsync failure")
            return real_fsync(descriptor)

        with mock_patch("eidos_runtime.tools.workspace.os.fsync", side_effect=fail_parent_fsync):
            result = self.executor.commit_file_change(
                "write_file", prepared, threading.Event()
            )

        self.assertEqual(result["code"], "file_commit_uncertain")
        self.assertTrue(result["sideEffectsMayExist"])
        self.assertEqual((self.workspace / "durability.txt").read_text(), "committed\n")

    def test_eof_newline_change_has_unambiguous_approval_diff(self) -> None:
        target = self.workspace / "newline.txt"
        target.write_text("same", encoding="utf-8")

        prepared = self.executor.prepare_file_change(
            "write_file",
            {"path": "newline.txt", "content": "same\n"},
            threading.Event(),
        )

        assert not isinstance(prepared, dict)
        self.assertIn("Eidos EOF newline: before=absent, after=present", prepared.diff)
        self.assertNotIn("-same+same", prepared.diff)

    def test_line_ending_only_change_has_unambiguous_approval_diff(self) -> None:
        target = self.workspace / "line-endings.txt"
        target.write_bytes(b"first\r\nsecond\r\n")

        prepared = self.executor.prepare_file_change(
            "write_file",
            {"path": "line-endings.txt", "content": "first\nsecond\n"},
            threading.Event(),
        )

        assert not isinstance(prepared, dict)
        self.assertIn(
            "Eidos line endings: before=CRLF:2, after=LF:2",
            prepared.diff,
        )

    def test_file_change_content_with_invisible_control_characters_is_rejected(
        self,
    ) -> None:
        prepared = self.executor.prepare_file_change(
            "write_file",
            {"path": "control.txt", "content": "visible\x00hidden\n"},
            threading.Event(),
        )

        self.assertIsInstance(prepared, dict)
        assert isinstance(prepared, dict)
        self.assertEqual(prepared["code"], "unsupported_text_content")

    def test_hard_linked_file_metadata_fails_closed(self) -> None:
        target = self.workspace / "hardlink.txt"
        alias = self.workspace / "hardlink-alias.txt"
        target.write_text("base\n", encoding="utf-8")
        os.link(target, alias)

        prepared = self.executor.prepare_file_change(
            "write_file",
            {"path": "hardlink.txt", "content": "change\n"},
            threading.Event(),
        )

        self.assertIsInstance(prepared, dict)
        assert isinstance(prepared, dict)
        self.assertEqual(prepared["code"], "unsupported_file_metadata")

    @unittest.skipUnless(sys.platform == "darwin", "requires macOS file metadata")
    def test_extended_attribute_metadata_fails_closed(self) -> None:
        target = self.workspace / "xattr.txt"
        target.write_text("base\n", encoding="utf-8")
        completed = subprocess.run(
            ["/usr/bin/xattr", "-w", "com.eidos.test", "value", str(target)],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            self.skipTest(f"xattr fixture unavailable: {completed.stderr.strip()}")

        prepared = self.executor.prepare_file_change(
            "write_file",
            {"path": "xattr.txt", "content": "change\n"},
            threading.Event(),
        )

        self.assertIsInstance(prepared, dict)
        assert isinstance(prepared, dict)
        self.assertEqual(prepared["code"], "unsupported_file_metadata")

    @unittest.skipUnless(sys.platform == "darwin", "requires macOS file metadata")
    def test_access_control_list_metadata_fails_closed(self) -> None:
        target = self.workspace / "acl.txt"
        target.write_text("base\n", encoding="utf-8")
        completed = subprocess.run(
            ["/bin/chmod", "+a", "everyone deny write", str(target)],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            self.skipTest(f"ACL fixture unavailable: {completed.stderr.strip()}")

        prepared = self.executor.prepare_file_change(
            "write_file",
            {"path": "acl.txt", "content": "change\n"},
            threading.Event(),
        )

        self.assertIsInstance(prepared, dict)
        assert isinstance(prepared, dict)
        self.assertEqual(prepared["code"], "unsupported_file_metadata")

    def test_atomic_swap_rolls_back_a_post_approval_external_edit(self) -> None:
        target = self.workspace / "cas.txt"
        target.write_text("base\n", encoding="utf-8")
        prepared = self.executor.prepare_file_change(
            "write_file",
            {"path": "cas.txt", "content": "candidate\n"},
            threading.Event(),
        )
        assert not isinstance(prepared, dict)
        from eidos_runtime.sandbox.seatbelt import secure_workspace_move

        def edit_then_swap(workspace, source, destination, expected):
            target.write_text("external\n", encoding="utf-8")
            return secure_workspace_move(workspace, source, destination, expected)

        with mock_patch(
            "eidos_runtime.tools.workspace.secure_workspace_move",
            side_effect=edit_then_swap,
        ):
            result = self.executor.commit_file_change(
                "write_file", prepared, threading.Event()
            )

        self.assertEqual(result["code"], "file_version_conflict")
        self.assertFalse(result["sideEffectsMayExist"])
        self.assertEqual(target.read_text(), "external\n")

    def test_helper_conflict_or_failure_never_claims_matching_external_candidate(self) -> None:
        target = self.workspace / "matching-candidate.txt"

        for move_status, expected_code in (
            ("conflict", "file_version_conflict"),
            ("failed", "sandbox_unavailable"),
        ):
            with self.subTest(move_status=move_status):
                target.write_text("base\n", encoding="utf-8")
                prepared = self.executor.prepare_file_change(
                    "write_file",
                    {"path": target.name, "content": "candidate\n"},
                    threading.Event(),
                )
                assert not isinstance(prepared, dict)

                def external_candidate_then_report_status(
                    _workspace, _source, _destination, _expected
                ):
                    target.write_text("candidate\n", encoding="utf-8")
                    return move_status

                with mock_patch(
                    "eidos_runtime.tools.workspace.secure_workspace_move",
                    side_effect=external_candidate_then_report_status,
                ):
                    result = self.executor.commit_file_change(
                        "write_file", prepared, threading.Event()
                    )

                self.assertEqual(result["outcome"], "error")
                self.assertEqual(result["code"], expected_code)
                self.assertFalse(result["sideEffectsMayExist"])
                self.assertEqual(target.read_text(), "candidate\n")

    def test_uncertain_helper_result_is_never_upgraded_to_success(self) -> None:
        prepared = self.executor.prepare_file_change(
            "write_file",
            {"path": "uncertain.txt", "content": "candidate\n"},
            threading.Event(),
        )
        assert not isinstance(prepared, dict)
        from eidos_runtime.sandbox.seatbelt import secure_workspace_move

        def commit_then_report_uncertain(workspace, source, destination, expected):
            self.assertEqual(
                secure_workspace_move(workspace, source, destination, expected),
                "committed",
            )
            return "uncertain"

        with mock_patch(
            "eidos_runtime.tools.workspace.secure_workspace_move",
            side_effect=commit_then_report_uncertain,
        ):
            result = self.executor.commit_file_change(
                "write_file", prepared, threading.Event()
            )

        self.assertEqual(result["code"], "file_commit_uncertain")
        self.assertTrue(result["sideEffectsMayExist"])


class BlockingModel:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def complete(
        self, context, cancel, on_text_delta,
        *, instructions,
        allow_tools=True, tool_definitions=(),
    ):
        self.started.set()
        self.release.wait(timeout=2)
        return ModelResponse(text="Too late")


if __name__ == "__main__":
    unittest.main()
