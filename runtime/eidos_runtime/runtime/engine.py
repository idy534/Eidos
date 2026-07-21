from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Callable

from eidos_runtime.context.builder import (
    ContextBuilder,
    DEFAULT_CONTEXT_WINDOW_TOKENS,
    DEFAULT_REQUEST_MAX_OUTPUT_TOKENS,
)
from eidos_runtime.context.compactor import ContextCompactionError, ContextCompactor
from eidos_runtime.db.storage import (
    ContextLimitExceeded,
    InvalidRunStateError,
    RunLimitReached,
    SegmentLimitReached,
    SessionStore,
)
from eidos_runtime.model.client import ModelClient
from eidos_runtime.runtime.approval import ApprovalCoordinator, ApprovalDecision
from eidos_runtime.runtime.contracts import (
    LoopAction,
    RunContext,
    RunBudget,
    RuntimeCancelled,
)
from eidos_runtime.runtime.decision import LoopDecisionEngine
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.runtime.finalizer import RunFinalizer
from eidos_runtime.runtime.loop_guard import LoopGuard
from eidos_runtime.runtime.run_resources import RunResourceError, RunResources
from eidos_runtime.runtime.sampling import (
    SamplingAuthenticationFailed,
    SamplingCancelled,
    SamplingContextExceeded,
    SamplingError,
    SamplingInvalidRequest,
    SamplingProtocolError,
    SamplingRateLimited,
    SamplingRetryableError,
    SamplingRuntime,
)
from eidos_runtime.runtime.state_machine import RuntimePhaseTracker, RuntimeState
from eidos_runtime.runtime.step_context import StepContextFactory
from eidos_runtime.runtime.tool_runtime import ToolCallRuntime
from eidos_runtime.sandbox.sensitive import (
    SensitiveScanError,
    SensitiveScanner,
    default_scanner,
)


EMPTY_EXTENSION_SNAPSHOT = {
    "schemaVersion": 1,
    "extensionContractVersion": 1,
    "plugins": [],
    "skillCatalogHash": "",
    "mcpConfigHash": "",
}


class RuntimeEngine:
    def __init__(
        self,
        store: SessionStore,
        model: ModelClient,
        notify: Callable[[dict[str, object]], None],
        request_approval: Callable[
            [dict[str, object], threading.Event], ApprovalDecision
        ]
        | None = None,
        shell_available: bool = False,
        monotonic: Callable[[], float] = time.monotonic,
        sensitive: SensitiveScanner | None = None,
        wait_for_execution_slot: Callable[[str, threading.Event], bool] | None = None,
        mcp_sandbox: bool = True,
        context_window_tokens: int = DEFAULT_CONTEXT_WINDOW_TOKENS,
        request_max_output_tokens: int = DEFAULT_REQUEST_MAX_OUTPUT_TOKENS,
    ) -> None:
        self.store = store
        self.model = model
        self.events = RuntimeEvents(notify)
        self.request_approval = request_approval
        self.shell_available = shell_available
        self.monotonic = monotonic
        self.sensitive = sensitive or default_scanner()
        self.wait_for_execution_slot = wait_for_execution_slot
        self.mcp_sandbox = mcp_sandbox
        self.context_window_tokens = context_window_tokens
        self.request_max_output_tokens = request_max_output_tokens
        self.state_machine = RuntimePhaseTracker()
        self.active_started: float | None = None

    def run(self, run_id: str, cancel: threading.Event) -> None:
        run = self.store.read_run(run_id)
        self._emit_started(run_id, run)
        extension_snapshot = run.get("extensionSnapshot")
        if not isinstance(extension_snapshot, dict):
            extension_snapshot = dict(EMPTY_EXTENSION_SNAPSHOT)
        run_context = self._run_context(run, extension_snapshot)

        try:
            with RunResources(
                self.store,
                run_id,
                extension_snapshot,
                str(run.get("userInput") or ""),
                mcp_sandbox=self.mcp_sandbox,
            ) as resources:
                run_context = run_context.model_copy(
                    update={"skill_context": resources.skill_context}
                )
                self._drive(run_context, resources, cancel)
        except RunResourceError as error:
            self._fail(run_id, str(error))
        except (RuntimeCancelled, SamplingCancelled, InvalidRunStateError):
            self._cancel(run_id)
        finally:
            self._pause_effective_time(run_id)

    def _drive(
        self,
        run: RunContext,
        resources: RunResources,
        cancel: threading.Event,
    ) -> None:
        decisions = LoopDecisionEngine()
        guard = LoopGuard()
        context_builder = ContextBuilder(
            self.store,
            context_window_tokens=self.context_window_tokens,
            request_max_output_tokens=self.request_max_output_tokens,
        )
        compactor = ContextCompactor(self.store)
        step_factory = StepContextFactory(self.store)
        sampling = SamplingRuntime(
            self.store, self.model, self.events, self.sensitive
        )
        finalizer = RunFinalizer(
            self.store,
            self.model,
            self.events,
            self.sensitive,
            self.state_machine,
        )
        approval = ApprovalCoordinator(
            self.store,
            self.request_approval,
            self.events,
            self.state_machine,
            self._pause_effective_time,
            self._resume_effective_time,
            self._resume_after_approval,
            self._check_cancel,
            requeue=self.wait_for_execution_slot is not None,
        )

        while True:
            self._check_cancel(run.run_id, cancel)
            self._pause_effective_time(run.run_id)
            self.store.consume_pending_inputs(run.run_id)
            resources.refresh()
            dispatcher = resources.dispatcher
            if dispatcher is None:
                raise RuntimeError("run resources are not started")
            snapshot = dispatcher.snapshot(self.store.activated_tools(run.run_id))
            tool_definitions = tuple(
                dispatcher.model_definitions(snapshot.activated_names)
            )
            built = context_builder.build(
                run.run_id,
                tool_definitions=tool_definitions,
                skill_context=resources.skill_context,
                extra_context=run.model_context,
            )
            budget_fact = self.store.run_budget(run.run_id)
            compaction_guard = guard.observe_compaction_overflow(
                not built.budget.fits and built.facts.compaction_count > 0
            )
            context_decision = decisions.decide(
                context_budget=built.budget,
                run_budget=RunBudget.model_validate({
                    "segment_steps_remaining": budget_fact["segmentStepsRemaining"],
                    "run_steps_remaining": budget_fact["runStepsRemaining"],
                    "segment_effective_ms_remaining": budget_fact["segmentEffectiveMsRemaining"],
                    "run_effective_ms_remaining": budget_fact["runEffectiveMsRemaining"],
                }),
                compaction_count=built.facts.compaction_count,
                loop_guard_result=compaction_guard,
                pending_user_input=self.store.has_pending_input(run.run_id),
                cancelled=cancel.is_set(),
            )
            if context_decision.action == LoopAction.CANCEL:
                raise RuntimeCancelled
            if context_decision.action == LoopAction.COMPACT:
                phase = "mid_turn" if int(
                    self.store.read_run(run.run_id)["modelStepCount"]
                ) else "pre_turn"
                try:
                    compactor.compact(run.run_id, phase)
                except (ContextCompactionError, ContextLimitExceeded):
                    self._pause_run(run.run_id, "context_compaction_failed")
                    return
                continue
            if context_decision.action == LoopAction.FINALIZE:
                finalizer.finalize(
                    run.run_id,
                    built.model_context,
                    "max_effective_runtime"
                    if budget_fact["runEffectiveMsRemaining"] <= 0
                    else "max_total_steps",
                )
                return
            if context_decision.action == LoopAction.PAUSE:
                reason = context_decision.reason or "context_over_budget"
                if reason == "segment_budget_exhausted":
                    reason = (
                        "segment_time_limit"
                        if budget_fact["segmentEffectiveMsRemaining"] <= 0
                        else "segment_step_limit"
                    )
                self._pause_run(run.run_id, reason)
                return
            try:
                step = step_factory.create(
                    run,
                    resources,
                    model_context=built.model_context,
                    tool_snapshot=snapshot,
                    context_budget=built.budget,
                    workspace_version=built.facts.workspace_version,
                )
            except SegmentLimitReached as error:
                self._pause_for_segment_limit(run.run_id, error)
                return
            except RunLimitReached as error:
                finalizer.finalize(
                    run.run_id,
                    built.model_context,
                    "max_effective_runtime" if "time" in str(error)
                    else "max_total_steps",
                )
                return
            self._resume_effective_time()
            try:
                sampled = sampling.sample(step, cancel)
            except SensitiveScanError:
                self.store.complete_current_step(
                    run.run_id, "failed", reason="sensitive_scan_failed"
                )
                self._pause_run(run.run_id, "sensitive_scan_failed")
                return
            except SamplingError as error:
                self._handle_sampling_failure(run.run_id, error)
                return

            tools = ToolCallRuntime(
                self.store,
                dispatcher,
                approval,
                self.events,
                self.sensitive,
                self.state_machine,
                shell_available=self.shell_available,
            )
            validation = tools.validate(step, sampled)
            protocol_errors = 0
            if validation.status in {"validation_failed", "no_tools"} and (
                validation.status == "validation_failed"
                or sampled.assistant_item is None
            ):
                if sampled.assistant_item is not None:
                    mutation = self.store.complete_assistant_item_committed(
                        str(sampled.assistant_item["id"])
                    )
                    self.events.publish(mutation, item=mutation.value)
                protocol_errors = self.store.record_protocol_error(run.run_id)
                reason = validation.error_code or "empty_response"
                self.store.complete_current_step(run.run_id, "failed", reason=reason)
            else:
                self.store.clear_protocol_errors(run.run_id)

            guard_reason = guard.observe_empty_response(
                sampled.assistant_item is None and not sampled.tool_calls
            ) or guard.observe_protocol_error(validation.status == "validation_failed")
            decision = decisions.decide(
                sampling=sampled,
                tool_batch=validation,
                pending_user_input=self.store.has_pending_input(run.run_id),
                protocol_errors=protocol_errors,
                loop_guard_result=guard_reason,
                cancelled=cancel.is_set(),
            )
            if decision.reason == "pending_input":
                if sampled.assistant_item is not None:
                    mutation = self.store.complete_assistant_item_committed(
                        str(sampled.assistant_item["id"])
                    )
                    self.events.publish(mutation, item=mutation.value)
                self.store.complete_current_step(run.run_id, "completed")
                run = run.model_copy(update={"model_context": ()})
                continue
            if (
                decision.action == LoopAction.CONTINUE
                and validation.status != "ready"
            ):
                run = run.model_copy(update={
                    "model_context": (
                        *run.model_context,
                        {"type": "protocol_error", "code": decision.reason},
                    )
                })
                continue
            if decision.action == LoopAction.PAUSE:
                self.store.complete_current_step(
                    run.run_id, "failed", reason=decision.reason
                )
                self._pause_run(run.run_id, decision.reason or "loop_guard")
                return
            if decision.action == LoopAction.FAIL:
                self._fail(
                    run.run_id,
                    decision.failure.code if decision.failure else "INTERNAL_ERROR",
                )
                return
            if decision.action == LoopAction.COMPLETE:
                assert sampled.assistant_item is not None
                self.store.complete_current_step(run.run_id, "completed")
                mutation = self.store.complete_assistant_and_run_committed(
                    str(sampled.assistant_item["id"]), run.run_id
                )
                item, completed = mutation.value
                self.events.publish(mutation, item=item, run=completed)
                self.state_machine.track(RuntimeState.COMPLETED, "run_succeeded")
                return

            if sampled.assistant_item is not None:
                mutation = self.store.complete_assistant_item_committed(
                    str(sampled.assistant_item["id"])
                )
                self.events.publish(mutation, item=mutation.value)
            repeated = guard.observe_tool_calls(
                validation.tool_calls,
                step.workspace_version,
                step.reconciliation_epoch,
            )
            if repeated is not None:
                self.store.complete_current_step(run.run_id, "failed", reason=repeated)
                self._pause_run(run.run_id, repeated)
                return
            outcome = tools.execute(step, validation.tool_calls, cancel)
            guard_reason = guard.observe_errors(outcome.error_fingerprints)
            if guard_reason is None and outcome.status == "completed":
                guard_reason = guard.observe_progress(
                    outcome.workspace_version, outcome.diff_hash
                )
            tool_decision = decisions.decide(
                sampling=sampled,
                tool_batch=outcome,
                pending_user_input=self.store.has_pending_input(run.run_id),
                loop_guard_result=guard_reason,
                cancelled=cancel.is_set(),
            )
            if tool_decision.action == LoopAction.PAUSE:
                if self.store.read_run(run.run_id)["status"] == "running":
                    self._pause_run(
                        run.run_id, tool_decision.reason or "loop_guard"
                    )
                return
            if tool_decision.action == LoopAction.FAIL:
                self._fail(run.run_id, "INTERNAL_ERROR")
                return
            if outcome.status == "sensitive_rejected":
                run = run.model_copy(update={
                    "model_context": (*run.model_context, *outcome.feedback)
                })
                continue
            mutation = self._pause_effective_time(run.run_id)
            updated = self.store.read_run(run.run_id)
            if mutation is not None:
                self.events.publish(mutation, run=mutation.value)
            if updated["status"] == "waiting_user_input":
                return
            run = run.model_copy(update={"model_context": ()})

    def _run_context(
        self,
        run: dict[str, object],
        extension_snapshot: dict[str, object],
    ) -> RunContext:
        encoded = json.dumps(
            extension_snapshot,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return RunContext(
            run_id=str(run["id"]),
            session_id=str(run["sessionId"]),
            model_id=str(run["modelId"]),
            model_context=(),
            extension_snapshot=extension_snapshot,
            extension_snapshot_hash=hashlib.sha256(encoded).hexdigest(),
        )

    def _emit_started(self, run_id: str, run: dict[str, object]) -> None:
        user_item = self.store.get_user_item(run_id)
        self.events.publish_event(
            self.store.read_runtime_start_event(run_id),
            run=run,
            item=user_item,
        )

    def _handle_sampling_failure(self, run_id: str, error: SamplingError) -> None:
        if isinstance(error, SamplingRetryableError) and error.had_progress:
            self.store.complete_current_step(
                run_id, "failed", reason="model_stream_interrupted"
            )
            self._pause_run(run_id, "model_stream_interrupted")
            return
        code = "MODEL_REQUEST_FAILED"
        if isinstance(error, SamplingAuthenticationFailed):
            code = "MODEL_AUTHENTICATION_FAILED"
        elif isinstance(error, SamplingContextExceeded):
            code = "CONTEXT_INPUT_TOO_LARGE"
        elif isinstance(error, SamplingInvalidRequest):
            code = "MODEL_INVALID_REQUEST"
        elif isinstance(error, SamplingProtocolError):
            code = "MODEL_PROTOCOL_ERROR"
        elif isinstance(error, SamplingRateLimited):
            code = "MODEL_RATE_LIMITED"
        self._fail(run_id, code)

    def _pause_for_segment_limit(
        self, run_id: str, error: SegmentLimitReached
    ) -> None:
        reason = "segment_time_limit" if "time" in str(error) else "segment_step_limit"
        self._pause_run(run_id, reason)

    def _pause_run(self, run_id: str, reason: str) -> None:
        mutation = self.store.pause_run_committed(run_id, reason)
        self.state_machine.track(RuntimeState.WAITING_USER_INPUT, reason)
        self.events.publish(mutation, run=mutation.value)

    def _check_cancel(self, run_id: str, cancel: threading.Event) -> None:
        if cancel.is_set() or self.store.read_run(run_id)["status"] in {
            "canceled", "interrupted",
        }:
            raise RuntimeCancelled

    def _resume_after_approval(
        self, run_id: str, cancel: threading.Event
    ) -> None:
        current = self.store.read_run(run_id)
        if current["status"] != "queued":
            return
        if self.wait_for_execution_slot is not None:
            if not self.wait_for_execution_slot(run_id, cancel):
                raise RuntimeCancelled
            return
        claimed = self.store.claim_next_run()
        if claimed is None or claimed["id"] != run_id:
            raise InvalidRunStateError("run could not reacquire execution slot")

    def _resume_effective_time(self) -> None:
        self.active_started = self.monotonic()

    def _pause_effective_time(self, run_id: str):
        if self.active_started is None:
            return None
        elapsed_ms = max(
            0, int((self.monotonic() - self.active_started) * 1000 + 0.999)
        )
        self.active_started = None
        return self.store.add_effective_time_committed(run_id, elapsed_ms)

    def _fail(self, run_id: str, error_code: str) -> None:
        self.state_machine.track(RuntimeState.FAILED, error_code)
        self._pause_effective_time(run_id)
        self.store.complete_current_step(run_id, "failed", reason=error_code)
        mutation = self.store.fail_run_committed(run_id, error_code)
        items = {
            str(item["id"]): item
            for item in self.store.canceled_items_for_run(run_id)
        }
        self.events.publish(mutation, run=mutation.value, items=items)

    def _cancel(self, run_id: str) -> None:
        self.state_machine.track(RuntimeState.CANCELED, "run_canceled")
        self.store.complete_current_step(run_id, "canceled", reason="canceled")
        completed = self.store.read_run(run_id)
        if completed["status"] in {"running", "waiting_approval"}:
            mutation = self.store.cancel_run_committed(run_id)
            items = {
                str(item["id"]): item
                for item in self.store.canceled_items_for_run(run_id)
            }
            self.events.publish(mutation, run=mutation.value, items=items)


RuntimeLoop = RuntimeEngine
