from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import sys


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.db.storage import (  # noqa: E402
    InvalidRunStateError,
    SessionStore,
)
from eidos_runtime.extensions.plugins import PluginCatalog  # noqa: E402
from eidos_runtime.extensions.skills import SkillCatalog  # noqa: E402
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel  # noqa: E402
from eidos_runtime.model.config import default_profile_snapshot  # noqa: E402
from eidos_runtime.runtime.approval import (  # noqa: E402
    ApprovalCoordinator,
    ApprovalDecision,
)
from eidos_runtime.runtime.assistant_stream import AssistantStreamWriter  # noqa: E402
from eidos_runtime.runtime.contracts import (  # noqa: E402
    ProgressSignature,
    RuntimeCancelled,
)
from eidos_runtime.runtime.engine import RuntimeEngine  # noqa: E402
from eidos_runtime.runtime.events import RuntimeEvents  # noqa: E402
from eidos_runtime.runtime.finalizer import RunFinalizer  # noqa: E402
from eidos_runtime.runtime.tool_runtime import ReadOnlyToolHandler  # noqa: E402
from eidos_runtime.runtime.state_machine import RuntimePhaseTracker  # noqa: E402
from eidos_runtime.sandbox.sensitive import default_scanner  # noqa: E402
from eidos_runtime.tools.registry import (  # noqa: E402
    ToolProvenance,
    ToolRegistryEntry,
    ToolSpec,
)


class RuntimeHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-hardening-")
        root = Path(self.temporary.name)
        data = root / "data"
        workspace = root / "workspace"
        data.mkdir(mode=0o700)
        self.data = data
        workspace.mkdir()
        self.store = SessionStore(data)
        self.store.initialize()
        self.session = self.store.create_session(str(workspace))

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_finalization_user_cancel_wins_over_stopped(self) -> None:
        run = self._finalizing_run("cancel finalization")
        user_cancel = threading.Event()

        class CancelingModel:
            request_was_canceled = False

            def complete(model_self, _context, request_cancel, on_delta, **_kwargs):
                on_delta("safe progress\n")
                user_cancel.set()
                deadline = time.monotonic() + 1
                while not request_cancel.is_set() and time.monotonic() < deadline:
                    time.sleep(0.001)
                model_self.request_was_canceled = request_cancel.is_set()
                return ModelResponse()

        model = CancelingModel()
        RuntimeEngine(self.store, model, lambda _message: None).run(
            run["id"], user_cancel
        )

        self.assertTrue(model.request_was_canceled)
        self.assertEqual(self.store.read_run(run["id"])["status"], "canceled")
        assert self.store.connection is not None
        self.assertIsNone(self.store.connection.execute(
            "SELECT 1 FROM items WHERE run_id = ? AND status = 'in_progress'",
            (run["id"],),
        ).fetchone())

    def test_finalization_timeout_cancels_request_and_keeps_stopped(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "timeout finalization")

        class BlockingModel:
            request_was_canceled = False

            def complete(model_self, _context, request_cancel, _on_delta, **_kwargs):
                deadline = time.monotonic() + 1
                while not request_cancel.is_set() and time.monotonic() < deadline:
                    time.sleep(0.001)
                model_self.request_was_canceled = request_cancel.is_set()
                return ModelResponse()

        model = BlockingModel()
        finalizer = RunFinalizer(
            self.store,
            model,
            RuntimeEvents(lambda _message: None),
            default_scanner(),
            RuntimePhaseTracker(),
            timeout_seconds=0.01,
        )
        with self.assertLogs("eidos.runtime", level="WARNING") as logs:
            outcome = finalizer.finalize(
                run["id"], (), "max_total_steps", threading.Event()
            )

        self.assertTrue(model.request_was_canceled)
        self.assertEqual(outcome.run["status"], "stopped")
        self.assertEqual(outcome.failure_reason, "finalization_timeout")
        self.assertTrue(any("finalization_timeout" in line for line in logs.output))

    def test_finalizing_run_keeps_cancel_action(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "finalizing")
        self.store.begin_finalization(run["id"])

        self.assertEqual(
            self.store.read_run(run["id"])["allowedActions"], ["cancel"]
        )

    def test_finalization_streams_committed_item_lifecycle_without_magic_step(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "normal finalization")
        notifications: list[dict[str, object]] = []

        class StreamingModel:
            def complete(self, _context, _cancel, on_delta, **_kwargs):
                on_delta("first line\n")
                on_delta("second line")
                return ModelResponse(text="first line\nsecond line")

        outcome = RunFinalizer(
            self.store,
            StreamingModel(),
            RuntimeEvents(notifications.append),
            default_scanner(),
            RuntimePhaseTracker(),
        ).finalize(run["id"], (), "max_total_steps", threading.Event())

        self.assertEqual(outcome.run["status"], "stopped")
        self.assertEqual(outcome.item["content"], "first line\nsecond line")
        self.assertNotIn("modelStepIndex", outcome.item)
        methods = [message["method"] for message in notifications]
        self.assertIn("item/started", methods)
        self.assertIn("item/delta", methods)
        self.assertIn("item/completed", methods)
        self.assertLess(
            methods.index("item/started"),
            methods.index("item/delta"),
        )
        self.assertLess(
            methods.index("item/delta"),
            methods.index("item/completed"),
        )

    def test_finalization_sensitive_failure_never_commits_secret_or_active_item(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "sensitive finalization")
        secret = "sk-abcdefghijklmnop"

        class SensitiveModel:
            def complete(self, _context, _cancel, on_delta, **_kwargs):
                on_delta("safe line\n")
                on_delta(f"credential {secret}\n")
                return ModelResponse()

        outcome = RunFinalizer(
            self.store,
            SensitiveModel(),
            RuntimeEvents(lambda _message: None),
            default_scanner(),
            RuntimePhaseTracker(),
        ).finalize(run["id"], (), "max_total_steps", threading.Event())

        assert self.store.connection is not None
        rows = self.store.connection.execute(
            "SELECT status, content FROM items WHERE run_id = ?", (run["id"],)
        ).fetchall()
        self.assertEqual(outcome.failure_reason, "finalization_sensitive_content_rejected")
        self.assertTrue(all(row["status"] != "in_progress" for row in rows))
        self.assertNotIn(secret, "".join(str(row["content"] or "") for row in rows))

    def test_finalization_item_and_stopped_run_commit_atomically(self) -> None:
        run, writer = self._pending_finalization("atomic finalization")

        mutation = self.store.complete_finalization_and_stop_committed(
            str(writer.item["id"]), run["id"], "max_total_steps"
        )

        item, stopped = mutation.value
        self.assertEqual(item["status"], "completed")
        self.assertEqual(stopped["status"], "stopped")
        event_types = [event["eventType"] for event in mutation.events]
        self.assertIn("item.completed", event_types)
        self.assertIn("segment.status_changed", event_types)
        self.assertIn("run.status_changed", event_types)

    def test_finalization_terminal_event_failure_rolls_back_item_segment_and_run(self) -> None:
        run, writer = self._pending_finalization("rollback finalization")
        assert self.store.connection is not None
        before_events = self.store.connection.execute(
            "SELECT COUNT(*) FROM events WHERE run_id = ?", (run["id"],)
        ).fetchone()[0]

        with patch(
            "eidos_runtime.db.storage.append_event",
            side_effect=ValueError("fixture event failure"),
        ):
            with self.assertRaisesRegex(ValueError, "fixture event failure"):
                self.store.complete_finalization_and_stop_committed(
                    str(writer.item["id"]), run["id"], "max_total_steps"
                )

        self.assertEqual(self.store.read_run(run["id"])["status"], "finalizing")
        self.assertEqual(
            self.store.read_item(str(writer.item["id"]))["status"], "in_progress"
        )
        segment = self.store.connection.execute(
            "SELECT status FROM execution_segments WHERE run_id = ?", (run["id"],)
        ).fetchone()
        self.assertEqual(segment["status"], "running")
        self.assertEqual(
            self.store.connection.execute(
                "SELECT COUNT(*) FROM events WHERE run_id = ?", (run["id"],)
            ).fetchone()[0],
            before_events,
        )

    def test_finalization_cancel_and_stop_cas_allow_only_cancel_to_win(self) -> None:
        run, writer = self._pending_finalization("cancel wins")
        stop_entered = threading.Event()
        release_stop = threading.Event()
        errors: list[Exception] = []
        original = self.store.complete_finalization_and_stop_committed

        def delayed_stop(*args):
            stop_entered.set()
            release_stop.wait(1)
            return original(*args)

        def stop() -> None:
            try:
                self.store.complete_finalization_and_stop_committed(
                    str(writer.item["id"]), run["id"], "max_total_steps"
                )
            except Exception as error:
                errors.append(error)

        with patch.object(
            self.store, "complete_finalization_and_stop_committed", delayed_stop
        ):
            thread = threading.Thread(target=stop)
            thread.start()
            self.assertTrue(stop_entered.wait(1))
            self.store.cancel_run_committed(run["id"])
            release_stop.set()
            thread.join(1)

        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], InvalidRunStateError)
        self.assertEqual(self.store.read_run(run["id"])["status"], "canceled")
        self.assertEqual(
            self.store.read_item(str(writer.item["id"]))["status"], "canceled"
        )
        self.assertNotIn("errorCode", self.store.read_run(run["id"]))

    def test_finalization_cancel_and_stop_cas_allow_only_stop_to_win(self) -> None:
        run, writer = self._pending_finalization("stop wins")
        cancel_entered = threading.Event()
        release_cancel = threading.Event()
        errors: list[Exception] = []
        original = self.store.cancel_run_committed

        def delayed_cancel(*args):
            cancel_entered.set()
            release_cancel.wait(1)
            return original(*args)

        def cancel() -> None:
            try:
                self.store.cancel_run_committed(run["id"])
            except Exception as error:
                errors.append(error)

        with patch.object(self.store, "cancel_run_committed", delayed_cancel):
            thread = threading.Thread(target=cancel)
            thread.start()
            self.assertTrue(cancel_entered.wait(1))
            self.store.complete_finalization_and_stop_committed(
                str(writer.item["id"]), run["id"], "max_total_steps"
            )
            release_cancel.set()
            thread.join(1)

        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], InvalidRunStateError)
        self.assertEqual(self.store.read_run(run["id"])["status"], "stopped")
        self.assertEqual(
            self.store.read_item(str(writer.item["id"]))["status"], "completed"
        )
        self.assertNotIn("errorCode", self.store.read_run(run["id"]))

    def test_assistant_stream_abort_never_rewrites_completed_item(self) -> None:
        run, writer = self._pending_finalization("safe abort")
        self.store.complete_finalization_and_stop_committed(
            str(writer.item["id"]), run["id"], "max_total_steps"
        )

        self.assertIsNone(writer.abort())
        self.assertEqual(
            self.store.read_item(str(writer.item["id"]))["status"], "completed"
        )

    def test_approval_resolve_immediately_projects_approve_run_update(self) -> None:
        run, item, coordinator, notifications = self._approval_fixture("approve")

        coordinator.request(
            run["id"], item, {"kind": "file_change"}, threading.Event(),
            transition_reason="file_change_approval",
        )

        updates = [
            message for message in notifications if message["method"] == "run/updated"
        ]
        self.assertEqual(updates[-1]["params"]["run"]["status"], "running")

    def test_approval_resolve_immediately_projects_reject_run_update(self) -> None:
        run, item, coordinator, notifications = self._approval_fixture("reject")

        coordinator.request(
            run["id"], item, {"kind": "file_change"}, threading.Event(),
            transition_reason="file_change_approval",
        )

        updates = [
            message for message in notifications if message["method"] == "run/updated"
        ]
        self.assertEqual(updates[-1]["params"]["run"]["status"], "running")

    def test_second_reject_projects_waiting_user_input(self) -> None:
        run, first, coordinator, notifications = self._approval_fixture("reject")
        coordinator.request(
            run["id"], first, {"kind": "file_change"}, threading.Event(),
            transition_reason="file_change_approval",
        )
        second = self.store.create_tool_item(
            run["id"], 2, 0, "call-2", "write_file", "{}"
        )

        coordinator.request(
            run["id"], second, {"kind": "file_change"}, threading.Event(),
            transition_reason="file_change_approval",
        )

        updates = [
            message for message in notifications if message["method"] == "run/updated"
        ]
        self.assertEqual(
            updates[-1]["params"]["run"]["status"], "waiting_user_input"
        )

    def test_approval_notification_failure_does_not_roll_back_resolution(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "approve")
        step = self.store.increment_model_step(run["id"])
        item = self.store.create_tool_item(
            run["id"], step, 0, "call", "write_file", "{}"
        )
        sends = 0

        def notify(_message: dict[str, object]) -> None:
            nonlocal sends
            sends += 1
            if sends == 2:
                raise OSError("renderer disconnected")

        coordinator = self._coordinator("approve", RuntimeEvents(notify))
        with self.assertLogs("eidos.runtime", level="WARNING"):
            coordinator.request(
                run["id"], item, {"kind": "file_change"}, threading.Event(),
                transition_reason="file_change_approval",
            )

        self.assertEqual(self.store.read_run(run["id"])["status"], "running")
        self.assertEqual(
            self.store.read_item(item["id"])["toolCall"]["approvalDecision"],
            "approve",
        )

    def test_expired_approval_response_does_not_publish_resolved_state(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "late approval")
        step = self.store.increment_model_step(run["id"])
        item = self.store.create_tool_item(
            run["id"], step, 0, "call", "write_file", "{}"
        )
        cancel = threading.Event()
        notifications: list[dict[str, object]] = []

        def expire(_request: dict[str, object], _cancel: threading.Event):
            self.store.cancel_waiting_approval_committed(run["id"])
            cancel.set()
            return ApprovalDecision("approve")

        coordinator = ApprovalCoordinator(
            self.store,
            expire,
            RuntimeEvents(notifications.append),
            RuntimePhaseTracker(),
            lambda _run_id: None,
            lambda: None,
            lambda _run_id, _cancel: None,
            lambda _run_id, value: (_ for _ in ()).throw(RuntimeCancelled())
            if value.is_set()
            else None,
            requeue=False,
        )
        with self.assertRaises(RuntimeCancelled):
            coordinator.request(
                run["id"], item, {"kind": "file_change"}, cancel,
                transition_reason="file_change_approval",
            )

        self.assertEqual(
            [message["params"]["run"]["status"] for message in notifications],
            ["waiting_approval"],
        )

    def test_unexpected_state_conflict_fails_run_without_renderer_detail(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "state conflict")
        notifications: list[dict[str, object]] = []

        with (
            patch(
                "eidos_runtime.runtime.sampling.SamplingRuntime.sample",
                side_effect=__import__(
                    "eidos_runtime.db.storage", fromlist=["InvalidRunStateError"]
                ).InvalidRunStateError("fixture internal state detail"),
            ),
            self.assertLogs("eidos.runtime", level="ERROR"),
        ):
            RuntimeEngine(self.store, object(), notifications.append).run(
                run["id"], threading.Event()
            )

        failed = self.store.read_run(run["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["errorCode"], "RUNTIME_STATE_CONFLICT")
        self.assertNotIn("fixture internal state detail", json.dumps(notifications))

    def test_progress_signature_requires_three_truly_empty_rounds(self) -> None:
        from eidos_runtime.runtime.loop_guard import LoopGuard

        guard = LoopGuard()
        empty = ProgressSignature(
            workspace_version=0,
            diff_hash=None,
            successful_tool_result_hashes=(),
            new_context_fact_ids=(),
            error_fingerprints=(),
            resolved_error_fingerprints=(),
            reconciliation_epoch=0,
        )

        self.assertIsNone(guard.observe_progress(empty))
        self.assertIsNone(guard.observe_progress(empty))
        self.assertEqual(guard.observe_progress(empty), "no_progress")

    def test_new_successful_read_results_never_count_as_no_progress(self) -> None:
        from eidos_runtime.runtime.loop_guard import LoopGuard

        guard = LoopGuard()
        for index in range(4):
            signature = ProgressSignature(
                workspace_version=0,
                diff_hash=None,
                successful_tool_result_hashes=(f"result-{index}",),
                new_context_fact_ids=(f"fact-{index}",),
                error_fingerprints=(),
                resolved_error_fingerprints=(),
                reconciliation_epoch=0,
            )
            self.assertIsNone(guard.observe_progress(signature))

    def test_changed_diff_is_workspace_progress_even_without_version_change(self) -> None:
        from eidos_runtime.runtime.loop_guard import LoopGuard

        guard = LoopGuard()
        baseline = guard.make_signature(
            workspace_version=1,
            diff_hash="before",
            successful_tool_result_hashes=(),
            context_fact_ids=(),
            error_fingerprints=(),
            reconciliation_epoch=0,
        )
        guard.observe_progress(baseline)
        changed = guard.make_signature(
            workspace_version=1,
            diff_hash="after",
            successful_tool_result_hashes=(),
            context_fact_ids=(),
            error_fingerprints=(),
            reconciliation_epoch=0,
        )
        self.assertIsNone(guard.observe_progress(changed))
        self.assertEqual(guard._no_progress, 0)

    def test_loop_guard_recovers_recent_no_progress_rounds_from_steps(self) -> None:
        from eidos_runtime.runtime.loop_guard import LoopGuard

        run, _ = self.store.create_run(self.session["id"], "recover loop guard")
        signatures = []
        for _ in range(2):
            self.store.increment_model_step(run["id"])
            signature = ProgressSignature(
                workspace_version=0,
                diff_hash=None,
                successful_tool_result_hashes=(),
                new_context_fact_ids=(),
                error_fingerprints=(),
                resolved_error_fingerprints=(),
                reconciliation_epoch=0,
            )
            self.store.complete_current_step(
                run["id"], "completed", progress_signature=signature
            )
            signatures.append(signature)

        self.store.close()
        self.store = SessionStore(self.data)
        self.store.initialize()
        recovered = LoopGuard.from_signatures(
            self.store.recent_progress_signatures(run["id"])
        )
        self.assertEqual(recovered.observe_progress(signatures[-1]), "no_progress")

    def test_resolved_error_is_explicit_progress(self) -> None:
        from eidos_runtime.runtime.loop_guard import LoopGuard

        guard = LoopGuard()
        failed = guard.make_signature(
            workspace_version=0,
            diff_hash=None,
            successful_tool_result_hashes=(),
            context_fact_ids=("failed-fact",),
            error_fingerprints=("read-error",),
            reconciliation_epoch=0,
        )
        guard.observe_progress(failed)
        recovered = guard.make_signature(
            workspace_version=0,
            diff_hash=None,
            successful_tool_result_hashes=(),
            context_fact_ids=(),
            error_fingerprints=(),
            reconciliation_epoch=0,
        )

        self.assertEqual(recovered.resolved_error_fingerprints, ("read-error",))
        self.assertIsNone(guard.observe_progress(recovered))

    def test_repeated_error_counts_once_per_step_and_recovers_after_restart(self) -> None:
        from eidos_runtime.runtime.loop_guard import LoopGuard

        run, _ = self.store.create_run(self.session["id"], "error signatures")
        duplicate_batch = ("same", "same", "same")
        guard = LoopGuard()
        for expected in (None, None):
            self.store.increment_model_step(run["id"])
            signature = guard.make_signature(
                workspace_version=0,
                diff_hash=None,
                successful_tool_result_hashes=(),
                context_fact_ids=(),
                error_fingerprints=duplicate_batch,
                reconciliation_epoch=0,
            )
            self.assertEqual(guard.observe_progress(signature), expected)
            self.store.complete_current_step(
                run["id"], "completed", progress_signature=signature
            )

        self.store.close()
        self.store = SessionStore(self.data)
        self.store.initialize()
        recovered = LoopGuard.from_signatures(
            self.store.recent_progress_signatures(run["id"])
        )
        third = recovered.make_signature(
            workspace_version=0,
            diff_hash=None,
            successful_tool_result_hashes=(),
            context_fact_ids=(),
            error_fingerprints=duplicate_batch,
            reconciliation_epoch=0,
        )
        self.assertEqual(third.error_fingerprints, ("same",))
        self.assertEqual(recovered.observe_progress(third), "repeated_tool_error")

    def test_error_signature_resets_on_success_and_switches_on_different_error(self) -> None:
        from eidos_runtime.runtime.loop_guard import LoopGuard

        guard = LoopGuard()
        for errors in (("a",), ("a",), (), ("a",), ("b",), ("b",)):
            signature = guard.make_signature(
                workspace_version=0,
                diff_hash=None,
                successful_tool_result_hashes=(),
                context_fact_ids=(),
                error_fingerprints=errors,
                reconciliation_epoch=0,
            )
            self.assertNotEqual(
                guard.observe_progress(signature), "repeated_tool_error"
            )

    def test_parallel_safe_reads_reset_sensitive_input_streak(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "sensitive streak")
        secret_path = "sk-abcdefghijklmnop"
        model = ScriptedModel([
            ModelResponse(tool_calls=(
                ModelToolCall("secret-1", "read_file", {"path": secret_path}),
            )),
            ModelResponse(tool_calls=(
                ModelToolCall("read-1", "list_files", {}),
                ModelToolCall("read-2", "list_files", {}),
            )),
            ModelResponse(tool_calls=(
                ModelToolCall("secret-2", "read_file", {"path": secret_path}),
            )),
            ModelResponse(text="done"),
        ])

        RuntimeEngine(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )

        self.assertEqual(self.store.read_run(run["id"])["status"], "succeeded")
        assert self.store.connection is not None
        count = self.store.connection.execute(
            "SELECT consecutive_sensitive_tool_inputs FROM runs WHERE id = ?",
            (run["id"],),
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_context_facts_bound_long_session_and_keep_current_goal(self) -> None:
        old, _ = self.store.create_run(self.session["id"], "old")
        assert self.store.connection is not None
        now = int(time.time() * 1000)
        self.store.connection.executemany(
            """
            INSERT INTO items (
                id, session_id, run_id, ordinal, kind, status,
                content, incomplete, created_at, completed_at
            ) VALUES (?, ?, ?, ?, 'assistant_message', 'completed', ?, 0, ?, ?)
            """,
            (
                (
                    f"old-{index}", self.session["id"], old["id"], index + 2,
                    "x" * 5_000, now + index, now + index,
                )
                for index in range(250)
            ),
        )
        self.store.connection.commit()
        self.store.fail_run(old["id"], "fixture")
        current, _ = self.store.create_run(self.session["id"], "current goal")

        facts = self.store.context_projection_facts(current["id"])
        encoded = json.dumps(
            [item.model_dump() for item in facts.items],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

        self.assertTrue(facts.candidate_overflow)
        self.assertLessEqual(len(facts.items), 200)
        self.assertLessEqual(len(encoded), 768 * 1024)
        self.assertIn("current goal", [item.content for item in facts.items])

    def test_oversized_current_goal_fails_with_context_input_too_large(self) -> None:
        run, item = self.store.create_run(self.session["id"], "small")
        assert self.store.connection is not None
        oversized = "x" * (800 * 1024)
        self.store.connection.execute(
            "UPDATE runs SET user_input = ? WHERE id = ?", (oversized, run["id"])
        )
        self.store.connection.execute(
            "UPDATE items SET content = ? WHERE id = ?", (oversized, item["id"])
        )
        self.store.connection.commit()

        RuntimeEngine(self.store, ScriptedModel([]), lambda _message: None).run(
            run["id"], threading.Event()
        )

        failed = self.store.read_run(run["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["errorCode"], "CONTEXT_INPUT_TOO_LARGE")

    def test_two_compactions_still_over_budget_pauses_with_stable_reason(self) -> None:
        run, _ = self.store.create_run(
            self.session["id"],
            "x" * 20_000,
            model_profile=default_profile_snapshot("deepseek-v4-flash").model_copy(
                update={"context_window_tokens": 8_000, "max_output_tokens": 1_000}
            ),
        )
        assert self.store.connection is not None
        self.store.connection.execute(
            "UPDATE runs SET compaction_count = 2 WHERE id = ?", (run["id"],)
        )
        self.store.connection.commit()

        RuntimeEngine(self.store, ScriptedModel([]), lambda _message: None).run(
            run["id"], threading.Event()
        )

        paused = self.store.read_run(run["id"])
        self.assertEqual(paused["status"], "waiting_user_input")
        self.assertEqual(paused["pauseReason"], "context_still_over_budget")

    def test_steer_refreshes_skill_context_and_deferred_tools_next_step(self) -> None:
        plugin_root = Path(self.temporary.name) / "plugin"
        skill_root = plugin_root / "skills" / "review"
        skill_root.mkdir(parents=True)
        (skill_root / "SKILL.md").write_text(
            "---\nname: review\ndescription: Review files.\n---\nUse review checklist.\n",
            encoding="utf-8",
        )
        (plugin_root / "plugin.json").write_text(json.dumps({
            "schemaVersion": 1,
            "id": "demo",
            "name": "Demo",
            "version": "1.0.0",
            "description": "Fixture",
            "skills": [{"root": "skills/review"}],
            "mcpServers": [],
        }), encoding="utf-8")
        plugins = PluginCatalog(self.store)
        plugins.import_directory(plugin_root)
        plugins.set_enabled("demo", True)
        snapshot = SkillCatalog(plugins).extension_snapshot()
        run, _ = self.store.create_run(
            self.session["id"], "start without a skill", extension_snapshot=snapshot
        )
        model = ScriptedModel([
            ModelResponse(tool_calls=(ModelToolCall("read", "list_files", {}),)),
            ModelResponse(text="done"),
        ])
        deferred = _deferred_tool()
        original = ReadOnlyToolHandler.execute

        def steer(handler, run_id, *args):
            outcome = original(handler, run_id, *args)
            handler.dependencies.store.enqueue_input(run_id, "@demo:review")
            return outcome

        with (
            patch(
                "eidos_runtime.runtime.run_resources.McpManager.start",
                return_value=(deferred,),
            ),
            patch(
                "eidos_runtime.runtime.run_resources.McpManager.refresh_if_changed",
                return_value=None,
            ),
            patch.object(ReadOnlyToolHandler, "execute", steer),
        ):
            RuntimeEngine(self.store, model, lambda _message: None).run(
                run["id"], threading.Event()
            )

        first_tools = {
            value.name for value in model.tool_definitions_history[0]
        }
        second_tools = {
            value.name for value in model.tool_definitions_history[1]
        }
        self.assertNotIn("mcp__fixture__demo_deferred", first_tools)
        self.assertIn("mcp__fixture__demo_deferred", second_tools)
        self.assertNotIn("Use review checklist.", json.dumps(model.contexts[0]))
        self.assertIn("Use review checklist.", json.dumps(model.contexts[1]))

    def _finalizing_run(self, user_input: str) -> dict[str, object]:
        run, _ = self.store.create_run(self.session["id"], user_input)
        assert self.store.connection is not None
        self.store.connection.execute(
            "UPDATE runs SET model_step_count = 80 WHERE id = ?", (run["id"],)
        )
        self.store.connection.commit()
        return run

    def _pending_finalization(
        self, user_input: str
    ) -> tuple[dict[str, object], AssistantStreamWriter]:
        run, _ = self.store.create_run(self.session["id"], user_input)
        self.store.increment_model_step(run["id"])
        self.store.complete_current_step(run["id"], "completed")
        self.store.begin_finalization(run["id"])
        writer = AssistantStreamWriter(
            self.store,
            RuntimeEvents(lambda _message: None),
            run["id"],
            None,
        )
        writer.write("final delta")
        writer.flush()
        assert writer.item is not None
        return run, writer

    def _approval_fixture(self, decision: str):
        run, _ = self.store.create_run(self.session["id"], decision)
        step = self.store.increment_model_step(run["id"])
        item = self.store.create_tool_item(
            run["id"], step, 0, "call-1", "write_file", "{}"
        )
        notifications: list[dict[str, object]] = []
        coordinator = self._coordinator(decision, RuntimeEvents(notifications.append))
        return run, item, coordinator, notifications

    def _coordinator(
        self, decision: str, events: RuntimeEvents
    ) -> ApprovalCoordinator:
        return ApprovalCoordinator(
            self.store,
            lambda _request, _cancel: ApprovalDecision(decision),
            events,
            RuntimePhaseTracker(),
            lambda _run_id: None,
            lambda: None,
            lambda _run_id, _cancel: None,
            lambda _run_id, _cancel: None,
            requeue=False,
        )


class _DeferredAdapter:
    execution_kind = "read"

    def effective_arguments(self, arguments: object) -> dict[str, object] | None:
        return {} if arguments == {} else None

    def execute(
        self, _arguments: dict[str, object], _cancel: threading.Event
    ) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "toolContractVersion": 1,
            "toolName": "mcp__fixture__demo_deferred",
            "outcome": "success",
            "code": "ok",
            "summary": "ok",
            "data": {},
            "sideEffectsMayExist": False,
            "reconciliationRequired": False,
        }


def _deferred_tool() -> ToolRegistryEntry:
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    return ToolRegistryEntry(
        ToolSpec.model_validate({
            "name": "mcp__fixture__demo_deferred",
            "description": "Deferred fixture.",
            "sideEffect": "none",
            "approvalRequired": False,
            "timeoutSeconds": 5,
            "batchPolicy": "parallel",
            "visibility": "deferred",
            "inputSchema": schema,
            "resultSchema": schema,
        }),
        ToolProvenance.model_validate({
            "kind": "mcp",
            "sourceId": "demo",
            "sourceVersion": "1.0.0",
            "contentHash": "1" * 64,
            "pluginId": "demo",
            "serverId": "fixture",
        }),
        _DeferredAdapter(),
    )


if __name__ == "__main__":
    unittest.main()
