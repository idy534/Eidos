from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.sandbox.workspace_manifest import (  # noqa: E402
    WorkspaceManifest,
    attach_workspace_diff,
    capture_workspace_manifest,
    diff_workspace_manifests,
)
from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.model.client import ModelToolCall  # noqa: E402
from eidos_runtime.runtime.approval import (  # noqa: E402
    ApprovalCoordinator,
    ApprovalDecision,
)
from eidos_runtime.runtime.events import RuntimeEvents  # noqa: E402
from eidos_runtime.runtime.state_machine import RuntimePhaseTracker  # noqa: E402
from eidos_runtime.runtime.tool_dispatcher import ToolDispatcher  # noqa: E402
from eidos_runtime.runtime.tool_execution import ToolExecutionController  # noqa: E402
from eidos_runtime.runtime.tool_runtime import (  # noqa: E402
    ShellToolHandler,
    _HandlerDependencies,
)
from eidos_runtime.sandbox.sensitive import default_scanner  # noqa: E402
from eidos_runtime.tools.workspace import ToolExecutor  # noqa: E402


class WorkspaceManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-manifest-")
        self.workspace = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_manifest_is_stable_bounded_and_ignores_caches(self) -> None:
        (self.workspace / "b.txt").write_text("b", encoding="utf-8")
        (self.workspace / "a.txt").write_text("a", encoding="utf-8")
        (self.workspace / ".git").mkdir()
        (self.workspace / ".git" / "index").write_text("ignored", encoding="utf-8")
        (self.workspace / "node_modules").mkdir()
        (self.workspace / "node_modules" / "x").write_text("ignored", encoding="utf-8")

        manifest = capture_workspace_manifest(self.workspace)

        self.assertTrue(manifest.complete)
        self.assertFalse(manifest.truncated)
        self.assertEqual(
            tuple(entry.path for entry in manifest.entries), ("a.txt", "b.txt")
        )
        self.assertTrue(all(entry.sha256 is not None for entry in manifest.entries))

    def test_manifest_does_not_follow_workspace_symlink(self) -> None:
        outside = self.workspace.parent / f"{self.workspace.name}-outside"
        outside.mkdir()
        try:
            (outside / "secret.txt").write_text("secret", encoding="utf-8")
            os.symlink(outside, self.workspace / "link")

            manifest = capture_workspace_manifest(self.workspace)

            self.assertEqual(tuple(entry.path for entry in manifest.entries), ("link",))
            self.assertIsNone(manifest.entries[0].sha256)
        finally:
            (outside / "secret.txt").unlink()
            outside.rmdir()

    def test_diff_reports_created_modified_deleted_and_stable_hash(self) -> None:
        (self.workspace / "modified.txt").write_text("before", encoding="utf-8")
        (self.workspace / "deleted.txt").write_text("delete", encoding="utf-8")
        before = capture_workspace_manifest(self.workspace)
        (self.workspace / "modified.txt").write_text("after", encoding="utf-8")
        (self.workspace / "deleted.txt").unlink()
        (self.workspace / "created.txt").write_text("create", encoding="utf-8")
        after = capture_workspace_manifest(self.workspace)

        first = diff_workspace_manifests(before, after)
        second = diff_workspace_manifests(before, after)

        self.assertEqual(first.created, ("created.txt",))
        self.assertEqual(first.modified, ("modified.txt",))
        self.assertEqual(first.deleted, ("deleted.txt",))
        self.assertEqual(first.diff_hash, second.diff_hash)
        self.assertTrue(first.complete)

    def test_entry_limit_marks_manifest_incomplete(self) -> None:
        for index in range(3):
            (self.workspace / f"{index}.txt").write_text("x", encoding="utf-8")

        manifest = capture_workspace_manifest(self.workspace, max_entries=2)

        self.assertFalse(manifest.complete)
        self.assertTrue(manifest.truncated)
        self.assertEqual(len(manifest.entries), 2)

    def test_scan_deadline_marks_manifest_incomplete(self) -> None:
        (self.workspace / "a.txt").write_text("a", encoding="utf-8")

        manifest = capture_workspace_manifest(
            self.workspace, deadline=time.monotonic() - 1
        )

        self.assertFalse(manifest.complete)

    def test_shell_result_records_manifest_facts(self) -> None:
        before = WorkspaceManifest((), True, False)
        after = WorkspaceManifest((), False, True)
        diff = diff_workspace_manifests(before, after)
        result = {
            "outcome": "error",
            "code": "timeout",
            "data": {"exitCode": None},
            "sideEffectsMayExist": True,
            "reconciliationRequired": False,
        }

        attached = attach_workspace_diff(result, diff)

        self.assertEqual(attached["data"]["commandOutcome"], "error")
        self.assertFalse(attached["data"]["workspaceChanged"])
        self.assertEqual(attached["data"]["workspaceDiffHash"], diff.diff_hash)
        self.assertFalse(attached["data"]["workspaceManifestComplete"])
        self.assertTrue(attached["data"]["workspaceDiffIncomplete"])
        self.assertFalse(attached["reconciliationRequired"])


class ShellManifestIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-shell-manifest-")
        root = Path(self.temporary.name)
        data = root / "data"
        self.workspace = root / "workspace"
        data.mkdir(mode=0o700)
        self.workspace.mkdir()
        self.store = SessionStore(data)
        self.store.initialize()
        session = self.store.create_session(str(self.workspace))
        self.run, _ = self.store.create_run(session["id"], "shell")
        self.store.increment_model_step(self.run["id"])
        self.executor = ToolExecutor(self.workspace)
        self.dispatcher = ToolDispatcher(self.executor.registry)
        self.events = RuntimeEvents(lambda _message: None)
        state = RuntimePhaseTracker()
        approval = ApprovalCoordinator(
            self.store,
            lambda _request, _cancel: ApprovalDecision("approve"),
            self.events,
            state,
            lambda _run_id: None,
            lambda: None,
            lambda _run_id, _cancel: None,
            lambda _run_id, _cancel: None,
            requeue=False,
        )
        handlers = {}
        self.controller = ToolExecutionController(
            self.store,
            self.dispatcher,
            handlers,
            self.events,
            default_scanner(),
            approval=approval,
        )
        dependencies = _HandlerDependencies(
            self.store,
            self.dispatcher,
            self.events,
            default_scanner(),
            True,
            self.controller.execute_side_effect,
        )
        handlers["shell"] = ShellToolHandler(dependencies)

    def tearDown(self) -> None:
        self.executor.close()
        self.store.close()
        self.temporary.cleanup()

    def _execute(
        self, result, *, mutate=None, output=(), cancel=None, observe=None
    ):
        item = self.store.create_tool_item(
            self.run["id"], 1, 0, "shell-call", "run_shell",
            json.dumps({"command": "fixture"}),
        )
        call = ModelToolCall(
            "shell-call", "run_shell",
            {"command": "fixture", "cwd": ".", "timeoutSeconds": 120},
        )

        def fake_shell(*args, **_kwargs):
            if mutate is not None:
                mutate()
            on_delta = args[5]
            for index, delta in enumerate(output):
                on_delta(delta)
                if observe is not None:
                    observe()
                if cancel is not None and index == 0:
                    cancel.set()
            normalized = dict(result)
            data = dict(result.get("data", {}))
            data.setdefault("truncated", False)
            data.setdefault(
                "termination",
                "exit" if result.get("outcome") == "success" else "fixture",
            )
            normalized["data"] = data
            return normalized

        with patch(
            "eidos_runtime.runtime.tool_runtime.run_shell",
            side_effect=fake_shell,
        ):
            return self.controller.execute(
                run_id=self.run["id"],
                item=item,
                call=call,
                plan=self.dispatcher.plan(call),
                cancel=cancel or threading.Event(),
                deadline=None,
            )

    def test_success_without_file_change_does_not_increment_workspace_version(self) -> None:
        self._execute({
            "outcome": "success",
            "code": "ok",
            "summary": "done",
            "data": {"exitCode": 0, "stdout": "", "stderr": ""},
            "sideEffectsMayExist": True,
        })
        self.assertEqual(
            self.store.context_projection_facts(self.run["id"]).workspace_version,
            0,
        )

    def test_failed_shell_with_file_change_increments_workspace_version(self) -> None:
        outcome = self._execute(
            {
                "outcome": "error",
                "code": "nonzero_exit",
                "summary": "failed",
                "data": {"exitCode": 1, "stdout": "", "stderr": ""},
                "sideEffectsMayExist": True,
            },
            mutate=lambda: (self.workspace / "changed.txt").write_text(
                "changed", encoding="utf-8"
            ),
        )
        self.assertTrue(outcome.result["data"]["workspaceChanged"])
        self.assertEqual(
            self.store.context_projection_facts(self.run["id"]).workspace_version,
            1,
        )
        self.assertTrue(self.store.read_run(self.run["id"])["sideEffectsMayExist"])

    def test_shell_safe_lines_stream_before_process_exit(self) -> None:
        seen_during_process = []

        def inspect_stream():
            item = self.store.connection.execute(
                """
                SELECT items.content FROM items
                JOIN tool_calls ON tool_calls.item_id = items.id
                WHERE tool_calls.provider_call_id = 'shell-call'
                """
            ).fetchone()
            seen_during_process.append(item["content"])

        result = {
            "outcome": "success", "code": "ok", "summary": "done",
            "data": {"exitCode": 0, "stdout": "first\nsecond\n", "stderr": ""},
            "sideEffectsMayExist": True,
        }
        self._execute(result, output=("first\n",), observe=inspect_stream)

        self.assertEqual(seen_during_process, ["first\n"])
        item = self.store.connection.execute(
            """
            SELECT items.content FROM items
            JOIN tool_calls ON tool_calls.item_id = items.id
            WHERE tool_calls.provider_call_id = 'shell-call'
            """
        ).fetchone()
        self.assertEqual(item["content"], "first\n")

    def test_shell_sensitive_output_is_not_streamed(self) -> None:
        outcome = self._execute(
            {
                "outcome": "success", "code": "ok", "summary": "done",
                "data": {"exitCode": 0, "stdout": "sk-1234567890123456\n", "stderr": ""},
                "sideEffectsMayExist": True,
            },
            output=("sk-1234567890123456\n",),
        )

        item = self.store.connection.execute(
            """
            SELECT items.content FROM items
            JOIN tool_calls ON tool_calls.item_id = items.id
            WHERE tool_calls.provider_call_id = 'shell-call'
            """
        ).fetchone()
        self.assertIsNone(item["content"])
        self.assertEqual(outcome.result["code"], "sensitive_content_rejected")

    def test_shell_delta_order_is_monotonic(self) -> None:
        self._execute(
            {
                "outcome": "success", "code": "ok", "summary": "done",
                "data": {"exitCode": 0, "stdout": "one\ntwo\n", "stderr": ""},
                "sideEffectsMayExist": True,
            },
            output=("one\n", "two\n"),
        )

        rows = self.store.connection.execute(
            """
            SELECT payload_json FROM events
            WHERE event_type = 'item.delta' ORDER BY id
            """
        ).fetchall()
        payloads = [json.loads(row["payload_json"]) for row in rows]
        self.assertEqual([payload["sequence"] for payload in payloads], [1, 2])
        self.assertEqual([payload["delta"] for payload in payloads], ["one\n", "two\n"])

    def test_shell_cancel_stops_future_deltas(self) -> None:
        cancel = threading.Event()
        self._execute(
            {
                "outcome": "error", "code": "canceled", "summary": "canceled",
                "data": {"exitCode": None, "stdout": "one\ntwo\n", "stderr": ""},
                "sideEffectsMayExist": True,
            },
            output=("one\n", "two\n"),
            cancel=cancel,
        )

        item = self.store.connection.execute(
            """
            SELECT items.content FROM items
            JOIN tool_calls ON tool_calls.item_id = items.id
            WHERE tool_calls.provider_call_id = 'shell-call'
            """
        ).fetchone()
        self.assertEqual(item["content"], "one\n")

    def test_shell_final_result_matches_streamed_output(self) -> None:
        outcome = self._execute(
            {
                "outcome": "success", "code": "ok", "summary": "done",
                "data": {"exitCode": 0, "stdout": "one\ntwo\n", "stderr": ""},
                "sideEffectsMayExist": True,
            },
            output=("one\n", "two\n"),
        )

        item = self.store.connection.execute(
            """
            SELECT items.content FROM items
            JOIN tool_calls ON tool_calls.item_id = items.id
            WHERE tool_calls.provider_call_id = 'shell-call'
            """
        ).fetchone()
        self.assertEqual(item["content"], outcome.result["data"]["stdout"])


if __name__ == "__main__":
    unittest.main()
