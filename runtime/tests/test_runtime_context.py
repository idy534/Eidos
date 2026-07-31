from __future__ import annotations

from pathlib import Path
import json
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.context.budget import estimate_context_budget  # noqa: E402
from eidos_runtime.context.builder import ContextBuilder  # noqa: E402
from eidos_runtime.context.compactor import ContextCompactor  # noqa: E402
from eidos_runtime.db.storage import (  # noqa: E402
    ContextLimitExceeded,
    RECENT_CONTEXT_STEPS,
    SessionStore,
)
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel  # noqa: E402
from eidos_runtime.model.config import default_profile_snapshot  # noqa: E402
from eidos_runtime.runtime.engine import RuntimeEngine  # noqa: E402
from eidos_runtime.runtime.contracts import (  # noqa: E402
    LoopAction,
    ProgressSignature,
    RunBudget,
)
from eidos_runtime.runtime.decision import LoopDecisionEngine  # noqa: E402
from eidos_runtime.runtime.tool_runtime import ReadOnlyToolHandler  # noqa: E402
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel  # noqa: E402
from eidos_runtime.runtime.loop_guard import LoopGuard  # noqa: E402


class ContextBudgetTests(unittest.TestCase):
    def test_budget_accepts_exact_limit_and_rejects_one_token_over(self) -> None:
        payload = []
        exact = estimate_context_budget(
            payload,
            context_window_tokens=4_096,
            request_max_output_tokens=3_006,
            message_count=0,
            tool_call_count=0,
            tool_result_count=0,
        )
        self.assertEqual(exact.estimated_input_tokens, 66)
        self.assertEqual(exact.usable_input_budget, 66)
        self.assertTrue(exact.fits)

        over = estimate_context_budget(
            payload,
            context_window_tokens=4_096,
            request_max_output_tokens=3_007,
            message_count=0,
            tool_call_count=0,
            tool_result_count=0,
        )
        self.assertFalse(over.fits)


class LoopGuardTests(unittest.TestCase):
    def test_same_tool_call_pauses_on_third_identical_workspace_view(self) -> None:
        guard = LoopGuard()
        call = ModelToolCall("call", "read_file", {"path": "a.txt"})

        self.assertIsNone(guard.observe_tool_calls((call,), 0, 0))
        self.assertIsNone(guard.observe_tool_calls((call,), 0, 0))
        self.assertEqual(
            guard.observe_tool_calls((call,), 0, 0),
            "repeated_tool_call",
        )

    def test_workspace_change_allows_the_same_tool_call_again(self) -> None:
        guard = LoopGuard()
        call = ModelToolCall("call", "read_file", {"path": "a.txt"})
        guard.observe_tool_calls((call,), 0, 0)
        guard.observe_tool_calls((call,), 0, 0)

        self.assertIsNone(guard.observe_tool_calls((call,), 1, 0))

    def test_no_progress_pauses_on_third_round(self) -> None:
        guard = LoopGuard()
        signature = ProgressSignature(
            workspace_version=0,
            diff_hash=None,
            successful_tool_result_hashes=(),
            new_context_fact_ids=(),
            error_fingerprints=(),
            resolved_error_fingerprints=(),
            reconciliation_epoch=0,
        )

        self.assertIsNone(guard.observe_progress(signature))
        self.assertIsNone(guard.observe_progress(signature))
        self.assertEqual(guard.observe_progress(signature), "no_progress")

    def test_workspace_change_is_progress_even_with_unchanged_diff_hash(self) -> None:
        guard = LoopGuard()
        for workspace_version in range(1, 5):
            self.assertIsNone(guard.observe_progress(ProgressSignature(
                workspace_version=workspace_version,
                diff_hash="a" * 64,
                successful_tool_result_hashes=(),
                new_context_fact_ids=(),
                error_fingerprints=(),
                resolved_error_fingerprints=(),
                reconciliation_epoch=0,
            )))

    def test_same_error_pauses_on_third_occurrence(self) -> None:
        guard = LoopGuard()
        self.assertIsNone(guard.observe_errors(("same",)))
        self.assertIsNone(guard.observe_errors(("same",)))
        self.assertEqual(guard.observe_errors(("same",)), "repeated_tool_error")

    def test_second_empty_response_pauses_instead_of_failing(self) -> None:
        guard = LoopGuard()
        self.assertIsNone(guard.observe_empty_response(True))
        self.assertEqual(
            guard.observe_empty_response(True), "repeated_empty_response"
        )

    def test_loop_decision_priority_is_cancel_guard_context_then_budget(self) -> None:
        decision = LoopDecisionEngine()
        exhausted = RunBudget(
            segment_steps_remaining=0,
            run_steps_remaining=0,
            segment_effective_ms_remaining=0,
            run_effective_ms_remaining=0,
        )
        over = estimate_context_budget(
            [],
            context_window_tokens=4_096,
            request_max_output_tokens=3_007,
            message_count=0,
            tool_call_count=0,
            tool_result_count=0,
        )
        self.assertEqual(
            decision.decide(
                cancelled=True,
                run_budget=exhausted,
                loop_guard_result="no_progress",
                context_budget=over,
            ).action,
            LoopAction.CANCEL,
        )
        self.assertEqual(
            decision.decide(run_budget=exhausted, context_budget=over).action,
            LoopAction.COMPACT,
        )
        fitting = estimate_context_budget(
            [],
            context_window_tokens=4_096,
            request_max_output_tokens=3_006,
            message_count=0,
            tool_call_count=0,
            tool_result_count=0,
        )
        self.assertEqual(
            decision.decide(run_budget=exhausted, context_budget=fitting).action,
            LoopAction.FINALIZE,
        )
        available = exhausted.model_copy(update={
            "segment_steps_remaining": 1,
            "run_steps_remaining": 1,
            "segment_effective_ms_remaining": 1,
            "run_effective_ms_remaining": 1,
        })
        self.assertEqual(
            decision.decide(
                run_budget=available,
                loop_guard_result="no_progress",
                context_budget=fitting,
            ).action,
            LoopAction.PAUSE,
        )
        self.assertEqual(
            decision.decide(run_budget=available, context_budget=over).action,
            LoopAction.COMPACT,
        )


class ContextPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-context-")
        root = Path(self.temporary.name)
        self.data = root / "data"
        workspace = root / "workspace"
        self.data.mkdir(mode=0o700)
        workspace.mkdir()
        self.store = SessionStore(self.data)
        self.store.initialize()
        self.session = self.store.create_session(str(workspace))

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_session_store_exposes_facts_not_model_projection(self) -> None:
        self.assertTrue(hasattr(SessionStore, "context_projection_facts"))
        self.assertTrue(hasattr(SessionStore, "compaction_candidate_facts"))
        self.assertFalse(hasattr(SessionStore, "context_facts"))
        self.assertFalse(hasattr(SessionStore, "model_context"))

    def test_compaction_summarizes_oldest_history_and_keeps_recent_facts_raw(self) -> None:
        old, _ = self.store.create_run(self.session["id"], "old root")
        assert self.store.connection is not None
        now = int(time.time() * 1000)
        self.store.connection.executemany(
            """
            INSERT INTO items (
                id, session_id, run_id, ordinal, kind, status,
                content, incomplete, created_at, completed_at
            ) VALUES (?, ?, ?, ?, 'user_message', 'completed', ?, 0, ?, ?)
            """,
            (
                (
                    f"old-{index}", self.session["id"], old["id"], index + 2,
                    f"old message {index} " + "x" * 4_000,
                    now + index, now + index,
                )
                for index in range(1, 251)
            ),
        )
        self.store.connection.commit()
        self.store.fail_run(old["id"], "fixture")
        current, _ = self.store.create_run(self.session["id"], "current goal")
        recent_ids: list[str] = []
        for index, value in enumerate(("A", "B", "C"), 1):
            item = self.store.create_tool_item(
                current["id"], index, 0, f"recent-{value}", "read_file", "{}"
            )
            self.store.complete_tool_item(
                item["id"],
                json.dumps({"outcome": "success", "data": {"value": value}}),
            )
            recent_ids.append(str(item["id"]))

        summary = ContextCompactor(self.store).compact(current["id"], "pre_turn")
        projected = self.store.context_projection_facts(current["id"])
        encoded = json.dumps(
            [item.model_dump() for item in projected.items], ensure_ascii=False
        )

        self.assertIn("old-1", summary.source_item_ids)
        self.assertTrue(set(recent_ids).isdisjoint(summary.source_item_ids))
        self.assertIn("current goal", encoded)
        recent_values = {
            json.loads(item.result_json)["data"]["value"]
            for item in projected.items
            if item.item_id in recent_ids
        }
        self.assertEqual(recent_values, {"A", "B", "C"})

    def test_compaction_candidates_exclude_latest_input_recent_steps_and_reconciliation(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "current goal")
        item_ids: dict[int, str] = {}
        for step in range(1, RECENT_CONTEXT_STEPS + 3):
            item = self.store.create_tool_item(
                run["id"], step, 0, f"call-{step}", "read_file", "{}"
            )
            result = {
                "outcome": "error" if step == 1 else "success",
                "code": "uncertain" if step == 1 else "ok",
                "reconciliationRequired": step == 1,
            }
            self.store.complete_tool_item(item["id"], json.dumps(result))
            item_ids[step] = str(item["id"])
        self.store.enqueue_input(run["id"], "latest steer")
        self.store.consume_pending_inputs(run["id"])
        assert self.store.connection is not None
        self.store.connection.execute(
            "UPDATE runs SET reconciliation_required = 1 WHERE id = ?", (run["id"],)
        )
        self.store.connection.commit()

        candidates = self.store.compaction_candidate_facts(run["id"])
        candidate_ids = {item.item_id for item in candidates.items}
        latest_steps = range(3, RECENT_CONTEXT_STEPS + 3)

        self.assertNotIn(item_ids[1], candidate_ids)
        self.assertIn(item_ids[2], candidate_ids)
        self.assertTrue(
            all(item_ids[step] not in candidate_ids for step in latest_steps)
        )
        self.assertNotIn(
            "latest steer", [item.content for item in candidates.items]
        )

    def test_context_builder_keeps_tool_call_and_result_together(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "inspect")
        item = self.store.create_tool_item(
            run["id"], 1, 0, "call-1", "read_file", '{"path":"a.txt"}'
        )
        self.store.complete_tool_item(
            item["id"],
            json.dumps({"outcome": "success", "data": {"content": "ok"}}),
        )

        built = ContextBuilder(self.store).build(run["id"])
        call_index = next(
            index for index, value in enumerate(built.model_context)
            if value.get("type") == "tool_call"
        )
        self.assertEqual(built.model_context[call_index + 1]["type"], "tool_result")
        self.assertEqual(
            built.model_context[call_index]["callId"],
            built.model_context[call_index + 1]["callId"],
        )

    def test_context_projection_keeps_unresolved_errors_and_reconciliation_state(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "inspect failure")
        self.store.increment_model_step(run["id"])
        signature = ProgressSignature(
            workspace_version=0,
            diff_hash=None,
            successful_tool_result_hashes=(),
            new_context_fact_ids=(),
            error_fingerprints=("error-a",),
            resolved_error_fingerprints=(),
            reconciliation_epoch=1,
        )
        self.store.complete_current_step(
            run["id"], "completed", progress_signature=signature
        )
        assert self.store.connection is not None
        self.store.connection.execute(
            """
            UPDATE runs SET reconciliation_required = 1, reconciliation_epoch = 1
            WHERE id = ?
            """,
            (run["id"],),
        )
        self.store.connection.commit()

        built = ContextBuilder(self.store).build(run["id"])

        self.assertIn("error-a", str(built.model_context))
        self.assertIn('"reconciliationRequired":true', str(built.model_context))

    def test_pre_turn_compaction_persists_and_restores_structured_summary(self) -> None:
        old, _ = self.store.create_run(self.session["id"], "x" * 20_000)
        self.store.fail_run(old["id"], "fixture")
        current, _ = self.store.create_run(
            self.session["id"],
            "continue",
            model_profile=default_profile_snapshot("deepseek-v4-flash").model_copy(
                update={"context_window_tokens": 8_000, "max_output_tokens": 1_000}
            ),
        )
        builder = ContextBuilder(self.store)
        self.assertFalse(builder.build(current["id"]).budget.fits)

        summary = ContextCompactor(self.store).compact(current["id"], "pre_turn")
        self.assertIn("task_goal", summary.model_dump())
        self.assertTrue(builder.build(current["id"]).budget.fits)

        self.store.close()
        self.store = SessionStore(self.data)
        self.store.initialize()
        restored = self.store.latest_compact_summary(current["id"])
        self.assertEqual(restored, summary)

    def test_runtime_compacts_old_history_before_first_sampling(self) -> None:
        old, _ = self.store.create_run(self.session["id"], "x" * 20_000)
        self.store.fail_run(old["id"], "fixture")
        current, _ = self.store.create_run(
            self.session["id"],
            "continue",
            model_profile=default_profile_snapshot("deepseek-v4-flash").model_copy(
                update={"context_window_tokens": 20_000, "max_output_tokens": 1_000}
            ),
        )
        model = ScriptedModel([ModelResponse(text="done")])

        RuntimeEngine(self.store, model, lambda _message: None).run(
            current["id"], threading.Event()
        )

        self.assertEqual(self.store.read_run(current["id"])["status"], "succeeded")
        self.assertEqual(self.store.compaction_count(current["id"]), 1)
        self.assertIn("Compact summary:", str(model.contexts[0]))

    def test_runtime_preserves_recent_tool_result_and_finalizes_if_it_alone_is_too_large(self) -> None:
        workspace = Path(self.session["workspaceRoot"])
        (workspace / "large.txt").write_text("x" * 20_000)
        run, _ = self.store.create_run(
            self.session["id"],
            "read the large file",
            model_profile=default_profile_snapshot("deepseek-v4-flash").model_copy(
                update={"context_window_tokens": 25_000, "max_output_tokens": 1_000}
            ),
        )
        model = ScriptedModel([
            ModelResponse(tool_calls=(
                ModelToolCall("read", "read_file", {"path": "large.txt"}),
            )),
            ModelResponse(text="done"),
        ])

        RuntimeEngine(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )

        stopped = self.store.read_run(run["id"])
        self.assertEqual(stopped["status"], "stopped")
        self.assertEqual(stopped["stopReason"], "context_still_over_budget")
        self.assertEqual(self.store.compaction_count(run["id"]), 0)
        facts = self.store.context_projection_facts(run["id"])
        self.assertTrue(any("x" * 1_000 in (item.result_json or "") for item in facts.items))

    def test_compaction_event_failure_rolls_back_summary_and_count(self) -> None:
        old, _ = self.store.create_run(self.session["id"], "old")
        self.store.fail_run(old["id"], "fixture")
        current, _ = self.store.create_run(self.session["id"], "current")

        with patch(
            "eidos_runtime.db.repositories.context.append_event",
            side_effect=ValueError("fixture event failure"),
        ):
            with self.assertRaises(ValueError):
                ContextCompactor(self.store).compact(current["id"], "pre_turn")

        self.assertIsNone(self.store.latest_compact_summary(current["id"]))
        self.assertEqual(self.store.compaction_count(current["id"]), 0)

    def test_run_allows_at_most_two_automatic_compactions(self) -> None:
        old, _ = self.store.create_run(self.session["id"], "old")
        self.store.fail_run(old["id"], "fixture")
        current, _ = self.store.create_run(self.session["id"], "current")
        compactor = ContextCompactor(self.store)
        compactor.compact(current["id"], "pre_turn")
        for step in range(1, RECENT_CONTEXT_STEPS + 2):
            item = self.store.create_assistant_item(current["id"], step)
            self.store.append_item_content(item["id"], f"progress {step}")
            self.store.complete_assistant_item(item["id"])
        compactor.compact(current["id"], "mid_turn")
        another = self.store.create_assistant_item(
            current["id"], RECENT_CONTEXT_STEPS + 2
        )
        self.store.append_item_content(another["id"], "more")
        self.store.complete_assistant_item(another["id"])

        with self.assertRaises(ContextLimitExceeded):
            compactor.compact(current["id"], "mid_turn")

    def test_pending_input_is_injected_only_when_boundary_is_consumed(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "start")
        self.store.enqueue_input(run["id"], "steer")
        before = ContextBuilder(self.store).build(run["id"])
        self.assertNotIn("steer", [item.get("content") for item in before.model_context])
        self.assertTrue(self.store.has_pending_input(run["id"]))

        self.store.consume_pending_inputs(run["id"])
        after = ContextBuilder(self.store).build(run["id"])
        self.assertIn("steer", [item.get("content") for item in after.model_context])
        self.assertFalse(self.store.has_pending_input(run["id"]))

    def test_parallel_read_tools_run_together_and_persist_in_batch_order(self) -> None:
        (Path(self.session["workspaceRoot"]) / "a.txt").write_text("a")
        run, _ = self.store.create_run(self.session["id"], "read twice")
        model = ScriptedModel([
            ModelResponse(tool_calls=(
                ModelToolCall("second", "read_file", {"path": "a.txt"}),
                ModelToolCall("first", "list_files", {}),
            )),
            ModelResponse(text="done"),
        ])
        original = ReadOnlyToolHandler.execute
        lock = threading.Lock()
        active = 0
        maximum = 0
        worker_names: set[str] = set()

        def observed(handler, *args):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
                worker_names.add(threading.current_thread().name)
            time.sleep(0.05)
            try:
                return original(handler, *args)
            finally:
                with lock:
                    active -= 1

        kernel = RuntimeAsyncKernel()
        kernel.start()
        try:
            with patch.object(ReadOnlyToolHandler, "execute", observed):
                RuntimeEngine(
                    self.store,
                    model,
                    lambda _message: None,
                    async_kernel=kernel,
                ).run(run["id"], threading.Event())
        finally:
            kernel.close()

        self.assertEqual(maximum, 2)
        self.assertFalse(any(
            name.startswith("ThreadPoolExecutor-") for name in worker_names
        ))
        assert self.store.connection is not None
        rows = self.store.connection.execute(
            "SELECT provider_call_id, batch_order, status FROM tool_calls ORDER BY creation_seq"
        ).fetchall()
        self.assertEqual(
            [tuple(row) for row in rows],
            [("second", 0, "completed"), ("first", 1, "completed")],
        )

    def test_pending_input_arriving_during_tools_waits_for_next_sampling(self) -> None:
        (Path(self.session["workspaceRoot"]) / "a.txt").write_text("a")
        run, _ = self.store.create_run(self.session["id"], "read")
        model = ScriptedModel([
            ModelResponse(tool_calls=(
                ModelToolCall("call", "read_file", {"path": "a.txt"}),
            )),
            ModelResponse(text="done"),
        ])
        original = ReadOnlyToolHandler.execute

        def enqueue(handler, run_id, *args):
            outcome = original(handler, run_id, *args)
            handler.dependencies.store.enqueue_input(run_id, "steer now")
            return outcome

        with patch.object(ReadOnlyToolHandler, "execute", enqueue):
            RuntimeEngine(self.store, model, lambda _message: None).run(
                run["id"], threading.Event()
            )

        self.assertNotIn("steer now", [item.get("content") for item in model.contexts[0]])
        self.assertIn("steer now", [item.get("content") for item in model.contexts[1]])

    def test_pending_input_arriving_during_final_sampling_gets_another_boundary(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "start")
        store = self.store

        class SteeredModel:
            contexts = []
            calls = 0

            def complete(self, context, _cancel, on_text_delta, **_kwargs):
                self.contexts.append(context)
                self.calls += 1
                if self.calls == 1:
                    store.enqueue_input(run["id"], "steer during sampling")
                    on_text_delta("first answer")
                    return ModelResponse(text="first answer")
                on_text_delta("revised answer")
                return ModelResponse(text="revised answer")

        model = SteeredModel()
        RuntimeEngine(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )

        self.assertEqual(self.store.read_run(run["id"])["status"], "succeeded")
        self.assertEqual(model.calls, 2)
        self.assertIn(
            "steer during sampling",
            [item.get("content") for item in model.contexts[1]],
        )

    def test_cancellation_stops_a_running_parallel_read_batch(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "cancel reads")
        model = ScriptedModel([ModelResponse(tool_calls=(
            ModelToolCall("one", "list_files", {}),
            ModelToolCall("two", "list_files", {}),
        ))])
        cancel = threading.Event()

        def canceled(*_args):
            from eidos_runtime.runtime.contracts import RuntimeCancelled

            cancel.set()
            raise RuntimeCancelled

        with patch.object(ReadOnlyToolHandler, "execute", canceled):
            kernel = RuntimeAsyncKernel()
            kernel.start()
            try:
                RuntimeEngine(
                    self.store,
                    model,
                    lambda _message: None,
                    async_kernel=kernel,
                ).run(run["id"], cancel)
            finally:
                kernel.close()

        self.assertEqual(self.store.read_run(run["id"])["status"], "canceled")

    def test_side_effect_tool_in_a_batch_remains_exclusive(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "mixed batch")
        model = ScriptedModel([
            ModelResponse(tool_calls=(
                ModelToolCall("read", "list_files", {}),
                ModelToolCall(
                    "write", "write_file", {"path": "a.txt", "content": "x"}
                ),
            )),
            ModelResponse(text="cannot mix tools"),
        ])
        approvals: list[object] = []

        RuntimeEngine(
            self.store,
            model,
            lambda _message: None,
            lambda request, _cancel: approvals.append(request),  # type: ignore[arg-type]
        ).run(run["id"], threading.Event())

        assert self.store.connection is not None
        self.assertEqual(
            self.store.connection.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0],
            0,
        )
        self.assertEqual(approvals, [])

    def test_one_parallel_read_failure_does_not_cancel_the_other(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "read batch")
        model = ScriptedModel([
            ModelResponse(tool_calls=(
                ModelToolCall("bad", "read_file", {"path": "missing.txt"}),
                ModelToolCall("good", "list_files", {}),
            )),
            ModelResponse(text="done"),
        ])
        original = ReadOnlyToolHandler.execute

        def fail_one(handler, run_id, item, call, cancel, runtime):
            if call.provider_call_id == "bad":
                raise OSError("fixture failure")
            return original(handler, run_id, item, call, cancel, runtime)

        with patch.object(ReadOnlyToolHandler, "execute", fail_one):
            kernel = RuntimeAsyncKernel()
            kernel.start()
            try:
                RuntimeEngine(
                    self.store,
                    model,
                    lambda _message: None,
                    async_kernel=kernel,
                ).run(run["id"], threading.Event())
            finally:
                kernel.close()

        assert self.store.connection is not None
        rows = self.store.connection.execute(
            "SELECT provider_call_id, status FROM tool_calls ORDER BY batch_order"
        ).fetchall()
        self.assertEqual(
            [tuple(row) for row in rows],
            [("bad", "failed"), ("good", "completed")],
        )


if __name__ == "__main__":
    unittest.main()
