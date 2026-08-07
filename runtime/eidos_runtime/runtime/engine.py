from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from eidos_runtime.context.builder import ContextBuilder
from eidos_runtime.context.project_rules import ProjectRuleResolver
from eidos_runtime.context.compactor import ContextCompactionError, ContextCompactor
from eidos_runtime.db.storage import (
    ContextLimitExceeded,
    InvalidRunStateError,
    RunLimitReached,
    SegmentLimitReached,
    SessionStore,
)
from eidos_runtime.model.client import ModelClient
from eidos_runtime.domain.long_task import LongTaskStatus, SafePoint
from eidos_runtime.runtime.approval import ApprovalCoordinator, ApprovalDecision
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
from eidos_runtime.runtime.contracts import (
    LoopAction,
    RunContext,
    RunBudget,
    RuntimeCancelled,
)
from eidos_runtime.runtime.decision import LoopDecisionEngine
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.runtime.finalizer import RunFinalizer
from eidos_runtime.runtime.loop_guard import LoopGuard, tool_call_fingerprint
from eidos_runtime.runtime.run_resources import RunResourceError, RunResources
from eidos_runtime.runtime.resource_registry import ResourceRegistry
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
from eidos_runtime.runtime.tool_execution import ToolInfrastructureError
from eidos_runtime.sandbox.sensitive import (
    SensitiveScanError,
    SensitiveScanner,
    default_scanner,
)
from eidos_runtime.sandbox.permissions import (
    BasePermissionProfile,
    EffectivePermissionProfile,
)
from eidos_runtime.model.instructions import StepPermissionPolicy


EMPTY_EXTENSION_SNAPSHOT = {
    "schemaVersion": 1,
    "extensionContractVersion": 1,
    "plugins": [],
    "skillCatalogHash": "",
    "mcpConfigHash": "",
}

logger = logging.getLogger("eidos.runtime")


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
        terminalize_cancel: bool = True,
        async_kernel: RuntimeAsyncKernel | None = None,
        resource_registry: ResourceRegistry | None = None,
        events: RuntimeEvents | None = None,
    ) -> None:
        self.store = store
        self.model = model
        self.events = events or RuntimeEvents(notify, store=store)
        self.request_approval = request_approval
        self.shell_available = shell_available
        self.monotonic = monotonic
        self.sensitive = sensitive or default_scanner()
        self.wait_for_execution_slot = wait_for_execution_slot
        self.mcp_sandbox = mcp_sandbox
        self.terminalize_cancel = terminalize_cancel
        self.async_kernel = async_kernel
        self.resources = resource_registry or ResourceRegistry()
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
                async_kernel=self.async_kernel,
                mcp_sandbox=self.mcp_sandbox,
                resource_registry=self.resources,
            ) as resources:
                self._drive(run_context, resources, cancel)
        except RunResourceError as error:
            self._fail(run_id, str(error))
        except ContextLimitExceeded as error:
            current = self.store.read_run(run_id)
            if cancel.is_set() and current["status"] == "running":
                self._cancel(run_id)
            elif current["status"] != "running":
                pass
            elif error.reason == "current_user_goal":
                self._fail(run_id, "CONTEXT_INPUT_TOO_LARGE")
            else:
                self._fail(run_id, "CONTEXT_LIMIT_EXCEEDED")
        except (RuntimeCancelled, SamplingCancelled):
            if self.terminalize_cancel:
                self._cancel(run_id)
            else:
                raise RuntimeCancelled from None
        except InvalidRunStateError:
            logger.exception("Unexpected runtime state conflict")
            current = self.store.read_run(run_id)
            if current["status"] == "canceled":
                self._cancel(run_id)
            elif current["status"] != "interrupted":
                self._fail(run_id, "RUNTIME_STATE_CONFLICT")
        except ToolInfrastructureError:
            logger.exception("Tool infrastructure failed")
            current = self.store.read_run(run_id)
            if current["status"] in {
                "running",
                "waiting_approval",
                "finalizing",
            }:
                self._fail(run_id, "TOOL_INFRASTRUCTURE_FAILURE")
        finally:
            self._pause_effective_time(run_id)

    def _drive(
        self,
        run: RunContext,
        resources: RunResources,
        cancel: threading.Event,
    ) -> None:
        decisions = LoopDecisionEngine()
        guard = LoopGuard.from_signatures(
            self.store.recent_progress_signatures(run.run_id)
        )
        context_builder = ContextBuilder(self.store)
        compactor = ContextCompactor(self.store)
        step_factory = StepContextFactory(self.store)
        rule_resolver = ProjectRuleResolver()
        sampling = SamplingRuntime(self.store, self.model, self.events, self.sensitive)
        finalizer = RunFinalizer(
            self.store,
            self.model,
            self.events,
            self.sensitive,
            self.state_machine,
            resource_registry=self.resources,
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
            injected = self.store.consume_pending_input_facts(run.run_id)
            resources.refresh(tuple(content for _item_id, content in injected))
            dispatcher = resources.dispatcher
            if dispatcher is None:
                raise RuntimeError("run resources are not started")
            snapshot = dispatcher.snapshot(self.store.activated_tools(run.run_id))
            tool_definitions = tuple(
                dispatcher.model_definitions(snapshot.activated_names)
            )
            workspace = self.store.workspace_for_run(run.run_id)
            effective_cwd = self.store.effective_cwd_for_run(
                run.run_id, workspace_root=workspace.path
            )
            rule_snapshot = rule_resolver.resolve(
                workspace.path,
                effective_cwd,
            )
            step_policy = _build_step_policy(
                run.resolution_snapshot.permission_profile_json,
                run.resolution_snapshot.sandbox_policy_json,
                run.resolution_snapshot.workspace_identity.path,
                snapshot.activated_names,
            )
            built = context_builder.build(
                run.run_id,
                tool_definitions=tool_definitions,
                retained_context=resources.retained_context,
                selected_skill_context=resources.selected_skill_context,
                extra_context=run.model_context,
                rule_resolution_snapshot=rule_snapshot,
                step_policy=step_policy,
            )
            budget_fact = self.store.run_budget(run.run_id)
            compaction_guard = guard.observe_compaction_overflow(
                not built.budget.fits and built.facts.compaction_count > 0
            )
            context_decision = decisions.decide(
                context_budget=built.budget,
                run_budget=RunBudget.model_validate(
                    {
                        "segment_steps_remaining": budget_fact["segmentStepsRemaining"],
                        "run_steps_remaining": budget_fact["runStepsRemaining"],
                        "segment_effective_ms_remaining": budget_fact[
                            "segmentEffectiveMsRemaining"
                        ],
                        "run_effective_ms_remaining": budget_fact[
                            "runEffectiveMsRemaining"
                        ],
                    }
                ),
                compaction_count=built.facts.compaction_count,
                loop_guard_result=compaction_guard,
                pending_user_input=self.store.has_pending_input(run.run_id),
                cancelled=cancel.is_set(),
            )
            if context_decision.action == LoopAction.CANCEL:
                raise RuntimeCancelled
            if context_decision.action == LoopAction.COMPACT:
                phase = (
                    "mid_turn"
                    if int(self.store.read_run(run.run_id)["modelStepCount"])
                    else "pre_turn"
                )
                try:
                    compactor.compact(run.run_id, phase)
                except ContextLimitExceeded:
                    raise
                except ContextCompactionError:
                    finalizer.finalize(
                        run.run_id,
                        built.model_context,
                        "context_still_over_budget",
                        cancel,
                        instructions=built.instructions,
                    )
                    return
                continue
            if context_decision.action == LoopAction.FINALIZE:
                reason = (
                    "max_effective_runtime"
                    if budget_fact["runEffectiveMsRemaining"] <= 0
                    else "max_total_steps"
                )
                finalizer.finalize(
                    run.run_id,
                    built.model_context,
                    reason,
                    cancel,
                    instructions=built.instructions,
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
                finalizer.finalize(
                    run.run_id,
                    built.model_context,
                    reason,
                    cancel,
                    instructions=built.instructions,
                )
                return
            try:
                step = step_factory.create(
                    run,
                    resources,
                    model_context=built.model_context,
                    instructions=built.instructions,
                    tool_snapshot=snapshot,
                    rule_resolution_snapshot=rule_snapshot,
                    context_budget=built.budget,
                    workspace_version=built.facts.workspace_version,
                    new_user_input_ids=tuple(item_id for item_id, _content in injected),
                )
            except SegmentLimitReached as error:
                reason = (
                    "segment_time_limit"
                    if "time" in str(error)
                    else "segment_step_limit"
                )
                finalizer.finalize(
                    run.run_id,
                    built.model_context,
                    reason,
                    cancel,
                    instructions=built.instructions,
                )
                return
            except RunLimitReached as error:
                reason = (
                    "max_effective_runtime"
                    if "time" in str(error)
                    else "max_total_steps"
                )
                finalizer.finalize(
                    run.run_id,
                    built.model_context,
                    reason,
                    cancel,
                    instructions=built.instructions,
                )
                return

            tools = ToolCallRuntime(
                self.store,
                dispatcher,
                approval,
                self.events,
                self.sensitive,
                self.state_machine,
                shell_available=self.shell_available,
                base_permissions=BasePermissionProfile.model_validate_json(
                    step.resolution_snapshot.permission_profile_json
                ),
                async_kernel=self.async_kernel,
                resource_registry=self.resources,
            )
            self._resume_effective_time()

            while True:
                try:
                    self._pause_at(run.run_id, SafePoint.BEFORE_MODEL, cancel)
                    sampled = sampling.sample(step, cancel)
                    self._pause_at(run.run_id, SafePoint.AFTER_MODEL, cancel)
                except SamplingCancelled:
                    raise
                except SensitiveScanError:
                    self.store.complete_current_step(
                        run.run_id, "failed", reason="sensitive_scan_failed"
                    )
                    self._fail(run.run_id, "SENSITIVE_SCAN_FAILED")
                    return
                except SamplingError as error:
                    self._handle_sampling_failure(run.run_id, error)
                    return

                validation = tools.validate(step, sampled)
                if validation.status == "validation_failed":
                    reason = validation.error_code or "invalid_response"
                    protocol_errors = self.store.record_protocol_error(run.run_id)
                    should_retry = protocol_errors < 2
                    sampling.complete_attempt(
                        step,
                        sampled,
                        status="failed",
                        error_code=reason,
                        retry=should_retry,
                        retry_reason=(
                            "protocol_repair"
                            if should_retry
                            else "protocol_repair_exhausted"
                        ),
                    )
                    if should_retry:
                        self.store.start_retry_model_attempt(run.run_id)
                        step = step.model_copy(update={
                            "model_context": (
                                *step.model_context,
                                {"type": "protocol_error", "code": reason},
                            )
                        })
                        continue
                    self.store.complete_current_step(
                        run.run_id, "failed", reason=reason
                    )
                    self._fail(run.run_id, "MODEL_PROTOCOL_ERROR")
                    return

                if validation.status == "no_tools" and not sampled.text:
                    protocol_errors = self.store.record_protocol_error(run.run_id)
                    empty_reason = guard.observe_empty_response(True)
                    should_retry = protocol_errors < 2 and empty_reason is None
                    sampling.complete_attempt(
                        step,
                        sampled,
                        status="failed",
                        error_code="empty_response",
                        retry=should_retry,
                        retry_reason=(
                            "empty_response_repair"
                            if should_retry
                            else "empty_response_exhausted"
                        ),
                    )
                    if should_retry:
                        self.store.start_retry_model_attempt(run.run_id)
                        step = step.model_copy(update={
                            "model_context": (
                                *step.model_context,
                                {"type": "protocol_error", "code": "empty_response"},
                            )
                        })
                        continue
                    reason = empty_reason or "empty_response"
                    self.store.complete_current_step(
                        run.run_id, "failed", reason=reason
                    )
                    if empty_reason is not None:
                        finalizer.finalize(
                            run.run_id,
                            built.model_context,
                            empty_reason,
                            cancel,
                            instructions=built.instructions,
                        )
                    else:
                        self._fail(run.run_id, "MODEL_PROTOCOL_ERROR")
                    return

                guard.observe_empty_response(False)
                self.store.clear_protocol_errors(run.run_id)
                sampling.complete_attempt(
                    step,
                    sampled,
                    status="completed",
                    retry=False,
                    retry_reason="completed",
                )
                if validation.status == "no_tools" and sampled.text:
                    assistant_item = sampling.commit_assistant(
                        step, sampled.text, cancel
                    )
                    sampled = sampled.model_copy(
                        update={"assistant_item": assistant_item}
                    )
                break

            decision = decisions.decide(
                sampling=sampled,
                tool_batch=validation,
                pending_user_input=self.store.has_pending_input(run.run_id),
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
            if decision.action == LoopAction.CONTINUE and validation.status != "ready":
                run = run.model_copy(update={"model_context": ()})
                continue
            if decision.action == LoopAction.PAUSE:
                self.store.complete_current_step(
                    run.run_id, "failed", reason=decision.reason
                )
                finalizer.finalize(
                    run.run_id,
                    built.model_context,
                    decision.reason or "loop_guard",
                    cancel,
                    instructions=built.instructions,
                )
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

            # Text accompanying tool calls is provisional narration rather than a
            # committed conversation fact. Tool items provide durable progress;
            # only a validated no-tool response becomes an assistant message.
            repeated = guard.observe_tool_calls(
                validation.tool_calls,
                step.workspace_version,
                step.reconciliation_epoch,
            )
            if repeated is not None:
                self.store.complete_current_step(run.run_id, "failed", reason=repeated)
                finalizer.finalize(
                    run.run_id,
                    built.model_context,
                    repeated,
                    cancel,
                    instructions=built.instructions,
                )
                return
            self._pause_at(run.run_id, SafePoint.BEFORE_TOOL, cancel)
            outcome = tools.execute(step, validation.tool_calls, cancel)
            self._pause_at(run.run_id, SafePoint.AFTER_TOOL, cancel)
            signature = guard.make_signature(
                workspace_version=outcome.workspace_version,
                diff_hash=outcome.diff_hash,
                successful_tool_result_hashes=outcome.successful_tool_result_hashes,
                context_fact_ids=outcome.context_fact_ids,
                error_fingerprints=outcome.error_fingerprints,
                reconciliation_epoch=outcome.reconciliation_epoch,
                new_user_input_ids=step.new_user_input_ids,
                tool_call_fingerprint=tool_call_fingerprint(
                    validation.tool_calls,
                    step.workspace_version,
                    step.reconciliation_epoch,
                ),
            )
            guard_reason = guard.observe_progress(signature)
            if outcome.status in {"completed", "paused"}:
                self.store.complete_current_step(
                    run.run_id, "completed", progress_signature=signature
                )
            tool_decision = decisions.decide(
                sampling=sampled,
                tool_batch=outcome,
                pending_user_input=self.store.has_pending_input(run.run_id),
                loop_guard_result=guard_reason,
                cancelled=cancel.is_set(),
            )
            if tool_decision.action == LoopAction.PAUSE:
                if self.store.read_run(run.run_id)["status"] == "interrupted":
                    return
                finalizer.finalize(
                    run.run_id,
                    built.model_context,
                    tool_decision.reason or "loop_guard",
                    cancel,
                    instructions=built.instructions,
                )
                return
            if tool_decision.action == LoopAction.FAIL:
                self._fail(run.run_id, "INTERNAL_ERROR")
                return
            if outcome.status == "sensitive_rejected":
                run = run.model_copy(
                    update={"model_context": (*run.model_context, *outcome.feedback)}
                )
                continue
            mutation = self._pause_effective_time(run.run_id)
            if mutation is not None:
                self.events.publish(mutation, run=mutation.value)
            run = run.model_copy(update={"model_context": ()})

    def _run_context(
        self,
        run: dict[str, object],
        extension_snapshot: dict[str, object],
    ) -> RunContext:
        resolution = self.store.read_run_resolution_snapshot(str(run["id"]))
        return RunContext(
            run_id=str(run["id"]),
            session_id=str(run["sessionId"]),
            model_id=str(run["modelId"]),
            model_profile=self.store.read_model_profile(str(run["id"])),
            model_context=(),
            extension_snapshot=extension_snapshot,
            extension_snapshot_hash=resolution.extension_snapshot_hash,
            resolution_snapshot=resolution,
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
            self._fail(run_id, "MODEL_STREAM_INTERRUPTED")
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

    def _check_cancel(self, run_id: str, cancel: threading.Event) -> None:
        if cancel.is_set() or self.store.read_run(run_id)["status"] in {
            "canceled",
            "interrupted",
        }:
            raise RuntimeCancelled

    def _pause_at(
        self, run_id: str, safe_point: SafePoint, cancel: threading.Event
    ) -> None:
        repository = self.store.long_task_repository()
        progress = repository.read(run_id)
        if progress is None:
            return
        progress = repository.record_safe_point(run_id, safe_point)
        if progress.status is LongTaskStatus.PAUSE_REQUESTED:
            progress = repository.mark_paused(run_id, safe_point)
        while progress.status in {
            LongTaskStatus.PAUSED,
            LongTaskStatus.RESUME_REQUESTED,
        }:
            if cancel.wait(0.05):
                raise RuntimeCancelled
            progress = repository.read(run_id) or progress

    def _resume_after_approval(self, run_id: str, cancel: threading.Event) -> None:
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
            str(item["id"]): item for item in self.store.canceled_items_for_run(run_id)
        }
        self.events.publish(mutation, run=mutation.value, items=items)

    def _cancel(self, run_id: str) -> None:
        self.state_machine.track(RuntimeState.CANCELED, "run_canceled")
        self.store.complete_current_step(run_id, "canceled", reason="canceled")
        completed = self.store.read_run(run_id)
        if completed["status"] in {"running", "waiting_approval", "finalizing"}:
            mutation = self.store.cancel_run_committed(run_id)
            items = {
                str(item["id"]): item
                for item in self.store.canceled_items_for_run(run_id)
            }
            self.events.publish(mutation, run=mutation.value, items=items)


RuntimeLoop = RuntimeEngine


def _build_step_policy(
    permission_profile_json: str,
    sandbox_policy_json: str,
    workspace_root: str,
    available_tools: tuple[str, ...],
) -> StepPermissionPolicy:
    """Build a StepPermissionPolicy from the persisted run permission snapshots.

    All values are read from the immutable snapshots captured at run start;
    this never queries the DB or filesystem.
    """
    import json
    try:
        effective = EffectivePermissionProfile.model_validate_json(
            permission_profile_json
        )
        network_enabled = effective.network_enabled
        writable_roots = tuple(
            entry.resolved_path
            for entry in effective.entries
            if entry.access.value in {"write", "execute"}
        )
    except Exception:
        network_enabled = False
        writable_roots = (workspace_root,)

    try:
        sandbox_data = json.loads(sandbox_policy_json)
        sandbox_mode_raw: str = sandbox_data.get("sandboxMode", "none")
        allow_escalated = bool(sandbox_data.get("allowEscalated", False))
        allow_additional = bool(sandbox_data.get("allowAdditional", True))
        rejected_ids: tuple[str, ...] = tuple(
            str(rid) for rid in sandbox_data.get("rejectedApprovalIds", [])
        )
    except Exception:
        sandbox_mode_raw = "none"
        allow_escalated = False
        allow_additional = True
        rejected_ids = ()

    return StepPermissionPolicy(
        sandbox_mode=sandbox_mode_raw,
        workspace_root=workspace_root,
        writable_roots=writable_roots,
        network_enabled=network_enabled,
        allow_additional_permissions=allow_additional,
        allow_escalated_execution=allow_escalated,
        rejected_approval_ids=rejected_ids,
        available_tools=available_tools,
    )
