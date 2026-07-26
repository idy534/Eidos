from __future__ import annotations

from dataclasses import dataclass, replace
import json
import logging
import threading
import time
from typing import Mapping, Protocol

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import ModelToolCall
from eidos_runtime.runtime.approval import ApprovalTransportError
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
    RuntimeResourceKind,
)
from eidos_runtime.runtime.tool_dispatcher import ToolDispatchPlan, ToolDispatcher
from eidos_runtime.sandbox.sensitive import SensitiveScanner


_active_lock = threading.Lock()
_active_executions = 0
logger = logging.getLogger("eidos.runtime")


@dataclass(frozen=True)
class HandlerOutcome:
    result: dict[str, object]
    item_status: str
    tool_status: str = "completed"
    activations: tuple[str, ...] = ()
    workspace_changed: bool = False
    diff_hash: str | None = None
    item: dict[str, object] | None = None


class ToolHandler(Protocol):
    def execute(
        self,
        run_id: str,
        item: dict[str, object],
        call: ModelToolCall,
        cancel: threading.Event,
    ) -> HandlerOutcome: ...


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
        handlers: Mapping[str, ToolHandler],
        events: RuntimeEvents,
        sensitive: SensitiveScanner,
        *,
        monotonic=time.monotonic,
        resource_registry: ResourceRegistry | None = None,
    ) -> None:
        self.store = store
        self.dispatcher = dispatcher
        self.handlers = handlers
        self.events = events
        self.sensitive = sensitive
        self.monotonic = monotonic
        self.resources = resource_registry or ResourceRegistry()
        self._execution_state = threading.local()

    def begin_durable_intent(
        self, item_id: str, preconditions: dict[str, object]
    ) -> str:
        intent_id = self.store.begin_durable_intent(
            item_id, preconditions=preconditions
        )
        self._execution_state.intent_started = True
        return intent_id

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
        self._execution_state.intent_started = False
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
                handler = self.handlers.get(plan.execution_kind)
                if handler is None:
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
                        outcome = handler.execute(
                            run_id, item, call, controlled_cancel
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
                    except Exception:
                        logger.exception("Tool execution failed")
                        outcome = HandlerOutcome(
                            tool_result(
                                call.name,
                                "error",
                                "TOOL_EXECUTION_FAILED",
                                "Tool execution failed",
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
                result = tool_error(
                    call.name,
                    "TOOL_OUTPUT_TOO_LARGE",
                    "Tool result exceeded the safe size limit",
                )
                outcome = replace(outcome, item_status="failed", tool_status="failed")
            outcome = replace(outcome, result=result)
            duration_ms = max(0, int((self.monotonic() - started) * 1000))
            mutation = self.store.complete_tool_item_once_committed(
                str(item["id"]),
                json.dumps(
                    outcome.result,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                item_status=outcome.item_status,
                tool_status=outcome.tool_status,
                workspace_changed=outcome.workspace_changed,
                diff_hash=outcome.diff_hash,
                duration_ms=duration_ms,
            )
            self.events.publish(mutation, item=mutation.value)
            if raise_after_commit:
                raise RuntimeCancelled
            return replace(outcome, item=mutation.value)
        finally:
            self._execution_state.intent_started = False
            _execution_finished()
            resource.close()

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
