from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch as mock_patch


import sys

RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.model import ModelResponse, ModelToolCall, ScriptedModel  # noqa: E402
from eidos_runtime.runtime_loop import ApprovalDecision, RuntimeLoop  # noqa: E402
from eidos_runtime.storage import ActiveRunError, SessionStore  # noqa: E402
from eidos_runtime.tools import ToolExecutor  # noqa: E402


class RuntimeLoopTests(unittest.TestCase):
    def setUp(self) -> None:
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
                "item/started",
                "item/delta",
                "item/completed",
                "run/completed",
            ],
        )

    def test_second_active_run_is_rejected(self) -> None:
        self.store.create_run(self.session["id"], "First")

        with self.assertRaises(ActiveRunError):
            self.store.create_run(self.session["id"], "Second")

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

    def test_sensitive_and_escaping_paths_are_rejected(self) -> None:
        sensitive = self.executor.execute(
            "read_file", {"path": ".env"}, threading.Event()
        )
        escaping = self.executor.execute(
            "read_file", {"path": "../outside"}, threading.Event()
        )

        self.assertEqual(sensitive["code"], "sensitive_path")
        self.assertEqual(escaping["code"], "workspace_boundary_violation")

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
            from eidos_runtime.seatbelt import secure_workspace_move

            return secure_workspace_move(workspace, source, destination, expected)

        try:
            with mock_patch(
                "eidos_runtime.tools.secure_workspace_move",
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

        from eidos_runtime.seatbelt import secure_workspace_move

        def move_then_cancel(workspace, source, destination, expected):
            moved = secure_workspace_move(workspace, source, destination, expected)
            cancel.set()
            return moved

        with mock_patch(
            "eidos_runtime.tools.secure_workspace_move",
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

        with mock_patch("eidos_runtime.tools.os.fsync", side_effect=fail_parent_fsync):
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

    def test_atomic_swap_rolls_back_a_post_approval_external_edit(self) -> None:
        target = self.workspace / "cas.txt"
        target.write_text("base\n", encoding="utf-8")
        prepared = self.executor.prepare_file_change(
            "write_file",
            {"path": "cas.txt", "content": "candidate\n"},
            threading.Event(),
        )
        assert not isinstance(prepared, dict)
        from eidos_runtime.seatbelt import secure_workspace_move

        def edit_then_swap(workspace, source, destination, expected):
            target.write_text("external\n", encoding="utf-8")
            return secure_workspace_move(workspace, source, destination, expected)

        with mock_patch(
            "eidos_runtime.tools.secure_workspace_move",
            side_effect=edit_then_swap,
        ):
            result = self.executor.commit_file_change(
                "write_file", prepared, threading.Event()
            )

        self.assertEqual(result["code"], "file_version_conflict")
        self.assertFalse(result["sideEffectsMayExist"])
        self.assertEqual(target.read_text(), "external\n")

    def test_uncertain_helper_result_is_never_upgraded_to_success(self) -> None:
        prepared = self.executor.prepare_file_change(
            "write_file",
            {"path": "uncertain.txt", "content": "candidate\n"},
            threading.Event(),
        )
        assert not isinstance(prepared, dict)
        from eidos_runtime.seatbelt import secure_workspace_move

        def commit_then_report_uncertain(workspace, source, destination, expected):
            self.assertEqual(
                secure_workspace_move(workspace, source, destination, expected),
                "committed",
            )
            return "uncertain"

        with mock_patch(
            "eidos_runtime.tools.secure_workspace_move",
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

    def complete(self, context, cancel, on_text_delta):
        self.started.set()
        self.release.wait(timeout=2)
        return ModelResponse(text="Too late")


if __name__ == "__main__":
    unittest.main()
