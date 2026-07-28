from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import json
import logging
import sqlite3
import threading
import time
from typing import Callable

from pydantic import BaseModel, ConfigDict

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.db.errors import InvalidRunStateError, StorageError
from eidos_runtime.db.invariants import RuntimeInvariantError
from eidos_runtime.model.client import ModelToolCall
from eidos_runtime.runtime.approval import (
    ApprovalCoordinator,
    ApprovalOutcome,
    ApprovalTransportError,
)
from eidos_runtime.runtime.contracts import RuntimeCancelled
from eidos_runtime.runtime.errors import (
    bounded_tool_result,
    safe_tool_result,
    tool_error,
    tool_result,
)
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.runtime.resource_registry import (
    ResourceRegistry,
    ResourceRegistryError,
    RuntimeResourceKind,
)
from eidos_runtime.runtime.fault_injection import hit_fault
from eidos_runtime.runtime.tool_dispatcher import ToolDispatchPlan, ToolDispatcher
from eidos_runtime.sandbox.sensitive import SensitiveScanner
from eidos_runtime.tools.contracts import GENERIC_PROJECTOR
from eidos_runtime.tools.registry import ToolConcurrencyPolicy


_active_lock = threading.Lock()
_active_executions = 0
logger = logging.getLogger("eidos.runtime")


class ToolInfrastructureError(RuntimeError):
    pass


class ToolConcurrencyGate:
    """Small cancellation-aware gate for immutable descriptor policies."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._active = 0
        self._exclusive = False
        self._keys: set[str] = set()

    def acquire(
        self,
        policy: ToolConcurrencyPolicy,
        cancel: threading.Event,
    ) -> "_ToolPermit":
        keys = set((*policy.resource_keys, *policy.exclusive_keys))
        with self._condition:
            while (
                self._exclusive
                or policy.mode == "exclusive" and self._active > 0
                or bool(self._keys & keys)
                or self._active >= policy.max_concurrency
            ):
                if cancel.is_set():
                    raise RuntimeCancelled
                self._condition.wait(0.05)
            if cancel.is_set():
                raise RuntimeCancelled
            self._active += 1
            self._exclusive = policy.mode == "exclusive"
            self._keys.update(keys)
        return _ToolPermit(self, keys)

    def _release(self, keys: set[str]) -> None:
        with self._condition:
            self._active -= 1
            self._exclusive = False
            self._keys.difference_update(keys)
            self._condition.notify_all()

    @property
    def active_permits(self) -> int:
        with self._condition:
            return self._active


class _ToolPermit:
    def __init__(self, gate: ToolConcurrencyGate, keys: set[str]) -> None:
        self._gate = gate
        self._keys = keys
        self._closed = False

    def __enter__(self) -> "_ToolPermit":
        return self

    def __exit__(self, *_error: object) -> None:
        if not self._closed:
            self._closed = True
            self._gate._release(self._keys)


_INFRASTRUCTURE_ERRORS = (
    StorageError,
    sqlite3.Error,
    InvalidRunStateError,
    RuntimeInvariantError,
    ResourceRegistryError,
)


@dataclass(frozen=True)
class HandlerOutcome:
    result: dict[str, object]
    item_status: str
    tool_status: str = "completed"
    activations: tuple[str, ...] = ()
    workspace_changed: bool = False
    diff_hash: str | None = None
    item: dict[str, object] | None = None
    progress_fingerprint: str | None = None


class ToolExecutionPhase(StrEnum):
    VALIDATING = "validating"
    PREPARING = "preparing"
    WAITING_APPROVAL = "waiting_approval"
    INTENT_COMMITTED = "intent_committed"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMMITTING = "committing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    UNCERTAIN = "uncertain"


class PreparedToolExecution(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    approval_description: dict[str, object]
    intent_preconditions: dict[str, object]
    transition_reason: str
    approval_diff: str = ""
    base_sha256: str | None = None


class VerifiedToolExecutionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    result: dict[str, object]


class _DeadlineCancellation(threading.Event):
    def __init__(
        self,
        cancel: threading.Event,
        deadline: float,
        monotonic,
    ) -> None:
        super().__init__()
        self.cancel = cancel
        self.deadline = deadline
        self.monotonic = monotonic
        self.paused_at: float | None = None

    def suspend_deadline(self) -> None:
        if self.paused_at is None:
            self.paused_at = self.monotonic()

    def resume_deadline(self) -> None:
        if self.paused_at is not None:
            self.deadline += max(0.0, self.monotonic() - self.paused_at)
            self.paused_at = None

    def is_set(self) -> bool:
        return self.cancel.is_set() or (
            self.paused_at is None and self.monotonic() >= self.deadline
        )

    def wait(self, timeout: float | None = None) -> bool:
        if self.is_set():
            return True
        remaining = (
            None
            if self.paused_at is not None
            else max(0.0, self.deadline - self.monotonic())
        )
        return self.cancel.wait(
            remaining
            if timeout is None
            else timeout
            if remaining is None
            else min(timeout, remaining)
        ) or self.is_set()

    @property
    def reason(self) -> str | None:
        if self.cancel.is_set():
            return "cancel"
        if self.paused_at is None and self.monotonic() >= self.deadline:
            return "timeout"
        return None


class ToolExecutionController:
    """Owns one validated ToolCall from dispatch through terminal commit."""

    def __init__(
        self,
        store: SessionStore,
        dispatcher: ToolDispatcher,
        runtime_context: object,
        events: RuntimeEvents,
        sensitive: SensitiveScanner,
        *,
        approval: ApprovalCoordinator | None = None,
        monotonic=time.monotonic,
        resource_registry: ResourceRegistry | None = None,
    ) -> None:
        self.store = store
        self.dispatcher = dispatcher
        self.runtime_context = runtime_context
        self.events = events
        self.sensitive = sensitive
        self.approval = approval
        self.monotonic = monotonic
        self.resources = resource_registry or ResourceRegistry()
        self._execution_state = threading.local()

    @property
    def current_phase(self) -> ToolExecutionPhase | None:
        return getattr(self._execution_state, "phase", None)

    def begin_durable_intent(
        self, item_id: str, preconditions: dict[str, object]
    ) -> str:
        intent_id = self.store.begin_durable_intent(
            item_id, preconditions=preconditions
        )
        self._execution_state.intent_started = True
        return intent_id

    def execute_side_effect(
        self,
        *,
        run_id: str,
        item: dict[str, object],
        prepared: PreparedToolExecution,
        cancel: threading.Event,
        execute: Callable[[], dict[str, object]],
        verify: Callable[[dict[str, object]], VerifiedToolExecutionResult] | None = None,
    ) -> tuple[ApprovalOutcome, VerifiedToolExecutionResult | None]:
        if self.approval is None:
            raise RuntimeError("tool execution approval coordinator is unavailable")
        self._execution_state.phase = ToolExecutionPhase.WAITING_APPROVAL
        approval = self.approval.request(
            run_id,
            item,
            prepared.approval_description,
            cancel,
            diff=prepared.approval_diff,
            base_sha256=prepared.base_sha256,
            transition_reason=prepared.transition_reason,
        )
        if approval.decision != "approve":
            return approval, None
        self.begin_durable_intent(
            str(item["id"]), prepared.intent_preconditions
        )
        self._execution_state.phase = ToolExecutionPhase.INTENT_COMMITTED
        if not self.store.side_effect_authorized(str(item["id"])):
            raise RuntimeError("tool execution contract violation")
        self._execution_state.authorized_effects += 1
        self._execution_state.phase = ToolExecutionPhase.EXECUTING
        raw = execute()
        self._execution_state.phase = ToolExecutionPhase.VERIFYING
        return approval, (
            verify(raw) if verify is not None
            else VerifiedToolExecutionResult(result=raw)
        )

    def execute(
        self,
        *,
        run_id: str,
        item: dict[str, object],
        call: ModelToolCall,
        plan: ToolDispatchPlan,
        cancel: threading.Event,
        deadline: float | None,
    ) -> HandlerOutcome:
        started = self.monotonic()
        effective_deadline = min(
            deadline if deadline is not None else float("inf"),
            started + plan.timeout_seconds,
        )
        controlled_cancel = _DeadlineCancellation(
            cancel, effective_deadline, self.monotonic
        )
        runtime = (
            plan.descriptor.runtime
            if plan.descriptor is not None else None
        )
        self._execution_state.intent_started = False
        self._execution_state.authorized_effects = 0
        self._execution_state.cleanup_attempted = False
        self._execution_state.phase = ToolExecutionPhase.VALIDATING
        resource = self.resources.register(
            RuntimeResourceKind.TOOL_EXECUTION,
            owner_id=str(item["id"]),
            deadline=effective_deadline,
            cancel=cancel.set,
        )
        resource.start()
        _execution_started()
        try:
            raise_after_commit = False
            reason = controlled_cancel.reason
            if reason is not None:
                outcome = self._interrupted(
                    call, plan, reason, uncertain=False
                )
            elif (
                plan.side_effect != "none"
                and self.store.side_effects_blocked(run_id)
            ):
                outcome = HandlerOutcome(
                    tool_error(
                        call.name,
                        "TOOL_RECONCILIATION_REQUIRED",
                        "A previous side effect must be reconciled",
                    ),
                    "failed",
                    "failed",
                )
            elif not self.dispatcher.validate_execution(call, plan):
                outcome = HandlerOutcome(
                    tool_error(
                        call.name,
                        "TOOL_EXECUTION_FAILED",
                        "Tool call validation failed",
                    ),
                    "failed",
                    "failed",
                )
            else:
                self._execution_state.phase = ToolExecutionPhase.PREPARING
                if runtime is None:
                    outcome = HandlerOutcome(
                        tool_error(
                            call.name,
                            "TOOL_EXECUTION_FAILED",
                            "Tool is unavailable",
                        ),
                        "failed",
                        "failed",
                    )
                else:
                    try:
                        hit_fault("tool_block")
                        outcome = runtime.invoke(
                            self.runtime_context,
                            run_id,
                            item,
                            call,
                            controlled_cancel,
                        )
                        if not isinstance(outcome, HandlerOutcome):
                            raise RuntimeError("invalid tool runtime outcome")
                        hit_fault("tool_late_result")
                        if (
                            plan.side_effect != "none"
                            and self._execution_state.authorized_effects == 0
                            and (
                                outcome.result.get("outcome") == "success"
                                or outcome.result.get(
                                    "sideEffectsMayExist"
                                ) is True
                            )
                        ):
                            outcome = HandlerOutcome(
                                tool_error(
                                    call.name,
                                    "TOOL_EXECUTION_CONTRACT_VIOLATION",
                                    "Tool execution contract was violated",
                                ),
                                "failed",
                                "failed",
                            )
                    except RuntimeCancelled:
                        raise_after_commit = True
                        outcome = self._interrupted(
                            call,
                            plan,
                            controlled_cancel.reason or "cancel",
                            uncertain=bool(
                                self._execution_state.intent_started
                            ),
                        )
                    except ApprovalTransportError:
                        raise
                    except _INFRASTRUCTURE_ERRORS as error:
                        raise ToolInfrastructureError(
                            "TOOL_INFRASTRUCTURE_FAILURE"
                        ) from error
                    except Exception as error:
                        logger.exception("Tool execution failed")
                        contract_violation = (
                            "tool execution contract violation" in str(error)
                        )
                        outcome = HandlerOutcome(
                            tool_result(
                                call.name,
                                "error",
                                (
                                    "TOOL_EXECUTION_CONTRACT_VIOLATION"
                                    if contract_violation
                                    else "TOOL_EXECUTION_FAILED"
                                ),
                                (
                                    "Tool execution contract was violated"
                                    if contract_violation
                                    else "Tool execution failed"
                                ),
                                side_effects_may_exist=(
                                    plan.side_effect != "none"
                                ),
                                reconciliation_required=(
                                    plan.side_effect != "none"
                                ),
                            ),
                            "failed",
                            "failed",
                        )
                    reason = controlled_cancel.reason
                    if (
                        reason is not None
                        and not _already_interrupted(outcome.result, reason)
                    ):
                        outcome = self._interrupted(
                            call,
                            plan,
                            reason,
                            uncertain=(
                                plan.side_effect != "none"
                                or bool(self._execution_state.intent_started)
                                or outcome.result.get(
                                    "sideEffectsMayExist"
                                ) is True
                            ),
                        )
            if outcome.workspace_changed or outcome.diff_hash is not None:
                enriched = dict(outcome.result)
                data = dict(
                    enriched.get("data")
                    if isinstance(enriched.get("data"), dict)
                    else {}
                )
                data["workspaceChanged"] = outcome.workspace_changed
                if outcome.diff_hash is not None:
                    data["workspaceDiffHash"] = outcome.diff_hash
                enriched["data"] = data
                outcome = replace(outcome, result=enriched)
            # Contract failure cannot erase an already-authorized side effect.
            try:
                outcome = replace(
                    outcome,
                    result=self._validate_result(plan, outcome.result),
                )
            except (TypeError, ValueError):
                effects_possible = (
                    self._execution_state.authorized_effects > 0
                    or outcome.result.get("sideEffectsMayExist") is True
                )
                outcome = replace(
                    outcome,
                    result=tool_result(
                        call.name,
                        "error",
                        "TOOL_RESULT_CONTRACT_VIOLATION",
                        "Tool returned data that violated its contract",
                        side_effects_may_exist=effects_possible,
                        reconciliation_required=effects_possible,
                    ),
                    item_status="failed",
                    tool_status="failed",
                    workspace_changed=(
                        outcome.workspace_changed if effects_possible else False
                    ),
                )
            result = safe_tool_result(
                self.sensitive,
                call.name,
                bounded_tool_result(call.name, outcome.result),
            )
            if result.get("code") == "sensitive_content_rejected":
                if plan.side_effect != "none":
                    result = tool_result(
                        call.name,
                        "error",
                        "sensitive_content_rejected",
                        "Tool output was withheld",
                        side_effects_may_exist=True,
                        reconciliation_required=True,
                    )
                outcome = replace(
                    outcome, item_status="failed", tool_status="failed"
                )
            if result.get("code") == "tool_result_too_large":
                effects_possible = (
                    self._execution_state.authorized_effects > 0
                    or outcome.result.get("sideEffectsMayExist") is True
                )
                result = tool_result(
                    call.name,
                    "error",
                    "TOOL_OUTPUT_TOO_LARGE",
                    "Tool result exceeded the safe size limit",
                    side_effects_may_exist=effects_possible,
                    reconciliation_required=effects_possible,
                )
                outcome = replace(outcome, item_status="failed", tool_status="failed")
            outcome = replace(outcome, result=result)
            try:
                outcome = replace(
                    outcome,
                    result=self._validate_result(plan, outcome.result),
                )
                projection = self._project_result(plan, outcome.result)
            except (TypeError, ValueError):
                effects_possible = (
                    self._execution_state.authorized_effects > 0
                    or outcome.result.get("sideEffectsMayExist") is True
                )
                outcome = replace(
                    outcome,
                    result=tool_result(
                        call.name,
                        "error",
                        "TOOL_RESULT_PROJECTION_FAILED",
                        "Tool result could not be projected safely",
                        side_effects_may_exist=effects_possible,
                        reconciliation_required=effects_possible,
                    ),
                    item_status="failed",
                    tool_status="failed",
                )
                outcome = replace(
                    outcome,
                    result=self._validate_result(plan, outcome.result),
                )
                projection = GENERIC_PROJECTOR.project(
                    plan.descriptor, outcome.result
                )
            outcome = replace(
                outcome,
                progress_fingerprint=projection.progress_fingerprint,
            )
            if runtime is not None:
                self._execution_state.cleanup_attempted = True
                try:
                    runtime.cleanup(
                        self.runtime_context,
                        str(self.current_phase or ToolExecutionPhase.VERIFYING),
                    )
                except Exception as error:
                    raise ToolInfrastructureError(
                        "TOOL_CLEANUP_FAILED"
                    ) from error
            self._execution_state.phase = ToolExecutionPhase.COMMITTING
            duration_ms = max(0, int((self.monotonic() - started) * 1000))
            mutation = self.store.complete_tool_item_once_committed(
                str(item["id"]),
                json.dumps(
                    outcome.result,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                model_result_json=json.dumps(
                    projection.model_result,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                ui_result_json=json.dumps(
                    projection.ui_result,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                progress_fingerprint=projection.progress_fingerprint,
                item_status=outcome.item_status,
                tool_status=outcome.tool_status,
                workspace_changed=outcome.workspace_changed,
                diff_hash=outcome.diff_hash,
                duration_ms=duration_ms,
            )
            self.events.publish(
                mutation,
                item=mutation.value,
            )
            self._execution_state.phase = (
                ToolExecutionPhase.COMPLETED
                if outcome.tool_status == "completed"
                else ToolExecutionPhase.CANCELED
                if outcome.result.get("code") == "TOOL_CANCELED"
                else ToolExecutionPhase.UNCERTAIN
                if outcome.result.get("reconciliationRequired") is True
                else ToolExecutionPhase.FAILED
            )
            if raise_after_commit:
                raise RuntimeCancelled
            return replace(outcome, item=mutation.value)
        except ToolInfrastructureError:
            try:
                infrastructure_result = tool_result(
                    call.name,
                    "error",
                    "TOOL_INFRASTRUCTURE_FAILURE",
                    "Tool infrastructure failed",
                    side_effects_may_exist=(
                        plan.side_effect != "none"
                    ),
                    reconciliation_required=(
                        plan.side_effect != "none"
                    ),
                )
                infrastructure_projection = self._project_result(
                    plan, infrastructure_result
                )
                mutation = self.store.complete_tool_item_once_committed(
                    str(item["id"]),
                    json.dumps(
                        infrastructure_result,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    model_result_json=json.dumps(
                        infrastructure_projection.model_result,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    ui_result_json=json.dumps(
                        infrastructure_projection.ui_result,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    progress_fingerprint=(
                        infrastructure_projection.progress_fingerprint
                    ),
                    item_status="failed",
                    tool_status="failed",
                    workspace_changed=False,
                    diff_hash=None,
                    duration_ms=max(
                        0, int((self.monotonic() - started) * 1000)
                    ),
                )
                self.events.publish(
                    mutation,
                    item=mutation.value,
                )
            except _INFRASTRUCTURE_ERRORS:
                logger.exception(
                    "Tool infrastructure terminalization failed"
                )
            raise
        except _INFRASTRUCTURE_ERRORS as error:
            raise ToolInfrastructureError(
                "TOOL_INFRASTRUCTURE_FAILURE"
            ) from error
        finally:
            if (
                runtime is not None
                and not getattr(
                    self._execution_state, "cleanup_attempted", False
                )
            ):
                try:
                    runtime.cleanup(
                        self.runtime_context,
                        str(
                            self.current_phase
                            or ToolExecutionPhase.FAILED
                        ),
                    )
                except Exception:
                    logger.exception("Tool runtime cleanup failed")
            self._execution_state.intent_started = False
            self._execution_state.authorized_effects = 0
            self._execution_state.cleanup_attempted = False
            _execution_finished()
            resource.close()

    def _validate_result(
        self, plan: ToolDispatchPlan, result: dict[str, object]
    ) -> dict[str, object]:
        if plan.descriptor is None:
            raise ValueError("tool_contract_unavailable")
        validated = plan.descriptor.validate_result(result)
        if validated.get("toolName") != plan.descriptor.spec.name:
            raise ValueError("tool_result_name_mismatch")
        return validated

    def _project_result(
        self, plan: ToolDispatchPlan, result: dict[str, object]
    ):
        if plan.descriptor is None or plan.descriptor.projector is None:
            raise ValueError("tool_contract_unavailable")
        return plan.descriptor.projector.project(plan.descriptor, result)

    @staticmethod
    def _interrupted(
        call: ModelToolCall,
        plan: ToolDispatchPlan,
        reason: str,
        *,
        uncertain: bool | None = None,
    ) -> HandlerOutcome:
        canceled = reason == "cancel"
        uncertain = plan.side_effect != "none" if uncertain is None else uncertain
        return HandlerOutcome(
            tool_result(
                call.name,
                "error",
                "TOOL_CANCELED" if canceled else "TOOL_TIMEOUT",
                "Tool was canceled" if canceled else "Tool timed out",
                side_effects_may_exist=uncertain,
                reconciliation_required=uncertain,
            ),
            "canceled" if canceled else "failed",
            "canceled" if canceled else "failed",
        )


def _already_interrupted(result: dict[str, object], reason: str) -> bool:
    code = str(result.get("code", "")).lower()
    return (
        reason == "cancel"
        and ("cancel" in code or result.get("outcome") == "canceled")
    ) or (
        reason == "timeout"
        and ("timeout" in code or "timed_out" in code)
    )


def active_tool_execution_count() -> int:
    with _active_lock:
        return _active_executions


def _execution_started() -> None:
    global _active_executions
    with _active_lock:
        _active_executions += 1


def _execution_finished() -> None:
    global _active_executions
    with _active_lock:
        _active_executions -= 1
