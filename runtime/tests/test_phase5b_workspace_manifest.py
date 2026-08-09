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
    WorkspaceManifestEntry,
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
from eidos_runtime.sandbox.permissions import BasePermissionProfile  # noqa: E402
from eidos_runtime.sandbox.sensitive import default_scanner  # noqa: E402
from eidos_runtime.tools.workspace import (  # noqa: E402
    ToolExecutor,
    WorkspacePathError,
)


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

    def test_incomplete_baseline_does_not_report_whole_workspace_as_created(self) -> None:
        before = WorkspaceManifest((), False, True)
        after = WorkspaceManifest((WorkspaceManifestEntry(
            "runtime/eidos_runtime/runtime/engine.py",
            12,
            1,
            0o644,
            7,
            "a" * 64,
        ),), True, False)

        diff = diff_workspace_manifests(before, after)
        attached = attach_workspace_diff(
            {
                "outcome": "success",
                "code": "ok",
                "summary": "Command completed",
                "data": {"exitCode": 0, "termination": "exit"},
            },
            diff,
        )

        self.assertFalse(diff.changed)
        self.assertFalse(attached["data"]["workspaceChanged"])
        self.assertEqual(attached["data"]["workspaceChangeState"], "unknown")
        self.assertTrue(attached["data"]["workspaceDiffIncomplete"])
        self.assertFalse(attached["reconciliationRequired"])
        self.assertEqual(attached["data"]["created"], [])
        self.assertEqual(attached["data"]["modified"], [])
        self.assertEqual(attached["data"]["deleted"], [])

    def test_explicit_execution_uncertainty_survives_successful_exit(self) -> None:
        diff = diff_workspace_manifests(
            WorkspaceManifest((), False, True),
            WorkspaceManifest((), False, True),
        )

        attached = attach_workspace_diff(
            {
                "outcome": "success",
                "code": "ok",
                "summary": "Command completed with uncertain cleanup",
                "data": {"exitCode": 0, "termination": "exit"},
                "sideEffectsMayExist": True,
                "reconciliationRequired": True,
            },
            diff,
        )

        self.assertEqual(attached["data"]["workspaceChangeState"], "unknown")
        self.assertTrue(attached["sideEffectsMayExist"])
        self.assertTrue(attached["reconciliationRequired"])

    def test_complete_baseline_reports_real_mutation(self) -> None:
        before = WorkspaceManifest((WorkspaceManifestEntry(
            "before.txt", 1, 1, 0o644, 7, "a" * 64
        ),), True, False)
        after = WorkspaceManifest((
            WorkspaceManifestEntry("before.txt", 2, 2, 0o644, 7, "b" * 64),
            WorkspaceManifestEntry("created.txt", 1, 1, 0o644, 8, "c" * 64),
        ), True, False)

        attached = attach_workspace_diff(
            {"outcome": "success", "code": "ok", "data": {}},
            diff_workspace_manifests(before, after),
        )

        self.assertTrue(attached["data"]["workspaceChanged"])
        self.assertEqual(attached["data"]["workspaceChangeState"], "changed")
        self.assertEqual(attached["data"]["created"], ["created.txt"])
        self.assertEqual(attached["data"]["modified"], ["before.txt"])
        self.assertFalse(attached["reconciliationRequired"])

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
        self.assertEqual(attached["data"]["workspaceChangeState"], "unknown")
        self.assertTrue(attached["reconciliationRequired"])


class ShellManifestIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.seatbelt_ready = patch(
            "eidos_runtime.runtime.tool_runtime.is_seatbelt_ready",
            return_value=True,
        )
        self.seatbelt_ready.start()
        self.addCleanup(self.seatbelt_ready.stop)
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
        class RuntimeContext:
            handler = None

            def invoke_shell(inner, runtime, run_id, item, call, cancel):
                assert inner.handler is not None
                return inner.handler.execute(
                    run_id, item, call, cancel, runtime
                )

        runtime_context = RuntimeContext()
        self.controller = ToolExecutionController(
            self.store,
            self.dispatcher,
            runtime_context,
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
            self.controller.authorize_side_effect,
            base_permissions=BasePermissionProfile.model_validate_json(
                self.store.read_step_resolution_snapshots(
                    self.run["id"]
                )[0].permission_profile_json
            ),
        )
        runtime_context.handler = ShellToolHandler(dependencies)

    def tearDown(self) -> None:
        self.executor.close()
        self.store.close()
        self.temporary.cleanup()

    def _execute(
        self,
        result,
        *,
        mutate=None,
        output=(),
        cancel=None,
        observe=None,
        arguments=None,
        attempts=None,
    ):
        effective_arguments = arguments or {
            "command": "fixture",
            "cwd": ".",
            "timeoutSeconds": 120,
            "sandboxPermissions": "use_default",
            "additionalPermissions": None,
            "justification": None,
        }
        item = self.store.create_tool_item(
            self.run["id"], 1, 0, "shell-call", "run_shell",
            json.dumps(effective_arguments),
        )
        call = ModelToolCall(
            "shell-call", "run_shell", effective_arguments,
        )

        def fake_shell(*args, **_kwargs):
            if attempts is not None:
                attempts.append(args[8])
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

    def test_shell_process_starts_when_post_launch_index_is_incomplete(self) -> None:
        result = {
            "outcome": "success",
            "code": "ok",
            "summary": "Command completed",
            "data": {"exitCode": 0, "stdout": "shell-started", "stderr": ""},
            "sideEffectsMayExist": True,
        }

        with patch.object(
            self.executor,
            "refresh_workspace_index",
            side_effect=WorkspacePathError("WORKSPACE_INDEX_INCOMPLETE"),
        ):
            outcome = self._execute(
                result,
                output=("shell-started",),
                arguments={
                    "command": "ls -la",
                    "cwd": ".",
                    "timeoutSeconds": 120,
                    "sandboxPermissions": "use_default",
                    "additionalPermissions": None,
                    "justification": None,
                },
            )

        self.assertEqual(outcome.result["code"], "ok")
        self.assertEqual(outcome.result["data"]["workspaceChangeState"], "unknown")
        self.assertTrue(outcome.result["data"]["workspaceDiffIncomplete"])
        self.assertFalse(outcome.result["reconciliationRequired"])
        self.assertEqual(
            self.store.connection.execute(
                "SELECT status FROM durable_intents WHERE run_id = ?",
                (self.run["id"],),
            ).fetchone()[0],
            "completed",
        )
        self.assertEqual(
            tuple(self.store.connection.execute(
                """
                SELECT reconciliation_required, side_effects_may_exist
                FROM runs WHERE id = ?
                """,
                (self.run["id"],),
            ).fetchone()),
            (0, 0),
        )
        self.assertEqual(
            self.store.connection.execute(
                """
                SELECT COUNT(*) FROM events
                WHERE run_id = ? AND event_type = 'reconciliation.required'
                """,
                (self.run["id"],),
            ).fetchone()[0],
            0,
        )

    def test_compound_read_only_shell_starts_without_workspace_preflight(self) -> None:
        (self.workspace / "token_budget.rs").write_text(
            "pub const LIMIT: usize = 1;\n", encoding="utf-8"
        )
        attempts = []
        result = {
            "outcome": "success",
            "code": "ok",
            "summary": "Command completed",
            "data": {"exitCode": 0, "stdout": "ready", "stderr": ""},
            "sideEffectsMayExist": True,
        }

        with patch.object(
            self.executor,
            "_verify_shell_workspace",
            side_effect=WorkspacePathError("sensitive_workspace_content"),
        ):
            outcome = self._execute(
                result,
                arguments={
                    "command": (
                        "git status --short --branch && "
                        "find . -maxdepth 2 -type f"
                    ),
                    "cwd": ".",
                    "timeoutSeconds": 120,
                    "sandboxPermissions": "use_default",
                    "additionalPermissions": None,
                    "justification": None,
                },
                attempts=attempts,
            )

        self.assertEqual(len(attempts), 1)
        self.assertEqual(outcome.result["code"], "ok")

    def test_first_shell_change_with_incomplete_baseline_is_unknown(self) -> None:
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
        self.assertFalse(outcome.result["data"]["workspaceChanged"])
        self.assertEqual(outcome.result["data"]["workspaceChangeState"], "unknown")
        self.assertEqual(
            self.store.context_projection_facts(self.run["id"]).workspace_version,
            0,
        )
        self.assertTrue(outcome.result["reconciliationRequired"])
        self.assertTrue(self.store.read_run(self.run["id"])["sideEffectsMayExist"])

    def test_successful_mutating_shell_completes_intent_and_increments_version(self) -> None:
        self.executor.refresh_workspace_index(threading.Event())

        outcome = self._execute(
            {
                "outcome": "success",
                "code": "ok",
                "summary": "Command completed",
                "data": {"exitCode": 0, "stdout": "", "stderr": ""},
                "sideEffectsMayExist": True,
            },
            mutate=lambda: (self.workspace / "foo.txt").touch(),
        )

        self.assertTrue(outcome.result["data"]["workspaceChanged"])
        self.assertEqual(outcome.result["data"]["workspaceChangeState"], "changed")
        self.assertFalse(outcome.result["reconciliationRequired"])
        self.assertEqual(
            self.store.context_projection_facts(self.run["id"]).workspace_version,
            1,
        )
        self.assertEqual(
            self.store.connection.execute(
                "SELECT status FROM durable_intents WHERE run_id = ?",
                (self.run["id"],),
            ).fetchone()[0],
            "completed",
        )
        self.assertEqual(
            tuple(self.store.connection.execute(
                """
                SELECT reconciliation_required, side_effects_may_exist
                FROM runs WHERE id = ?
                """,
                (self.run["id"],),
            ).fetchone()),
            (0, 0),
        )

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

    def test_unsandboxed_shell_still_scans_sensitive_output(self) -> None:
        attempts = []
        outcome = self._execute(
            {
                "outcome": "success",
                "code": "ok",
                "summary": "done",
                "data": {
                    "exitCode": 0,
                    "stdout": "sk-1234567890123456\n",
                    "stderr": "",
                },
                "sideEffectsMayExist": True,
            },
            output=("sk-1234567890123456\n",),
            arguments={
                "command": "fixture",
                "cwd": ".",
                "timeoutSeconds": 120,
                "sandboxPermissions": "require_escalated",
                "additionalPermissions": None,
                "justification": "Native access is required",
            },
            attempts=attempts,
        )

        self.assertEqual(outcome.result["code"], "sensitive_content_rejected")
        self.assertEqual(attempts[0].sandbox.value, "none")
        self.assertTrue(outcome.result["sideEffectsMayExist"])

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
