from __future__ import annotations

import logging
import threading
import time

from pydantic import BaseModel, ConfigDict

from eidos_runtime.db.storage import InvalidRunStateError, SessionStore
from eidos_runtime.model.client import ModelClient, ModelContextItem
from eidos_runtime.runtime.assistant_stream import AssistantStreamWriter
from eidos_runtime.runtime.contracts import RuntimeCancelled
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.runtime.model_runner import ModelRunner, ModelStreamInterrupted
from eidos_runtime.runtime.resource_registry import (
    ResourceRegistry,
    RuntimeResourceKind,
)
from eidos_runtime.runtime.fault_injection import hit_fault
from eidos_runtime.runtime.state_machine import RuntimePhaseTracker, RuntimeState
from eidos_runtime.sandbox.sensitive import (
    SensitiveScanError,
    SensitiveScanner,
)


logger = logging.getLogger("eidos.runtime")
FINALIZATION_SECONDS = 60


class _CombinedCancellation(threading.Event):
    def __init__(self, *sources: threading.Event) -> None:
        super().__init__()
        self.sources = sources

    def is_set(self) -> bool:
        return super().is_set() or any(source.is_set() for source in self.sources)

    def wait(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                break
            super().wait(0.05 if remaining is None else min(0.05, remaining))
        return self.is_set()


class FinalizationOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    run: dict[str, object]
    item: dict[str, object] | None = None
    failure_reason: str | None = None


class RunFinalizer:
    """Runs one bounded, tool-less final response using step-less Item semantics."""

    def __init__(
        self,
        store: SessionStore,
        model: ModelClient,
        events: RuntimeEvents,
        sensitive: SensitiveScanner,
        state_machine: RuntimePhaseTracker,
        *,
        timeout_seconds: float = FINALIZATION_SECONDS,
        resource_registry: ResourceRegistry | None = None,
    ) -> None:
        self.store = store
        self.model = model
        self.events = events
        self.sensitive = sensitive
        self.state_machine = state_machine
        self.timeout_seconds = timeout_seconds
        self.resources = resource_registry or ResourceRegistry()

    def finalize(
        self,
        run_id: str,
        context: tuple[ModelContextItem, ...],
        stop_reason: str,
        cancel: threading.Event,
    ) -> FinalizationOutcome:
        resource = self.resources.register(
            RuntimeResourceKind.FINALIZATION,
            owner_id=run_id,
            cancel=cancel.set,
        )
        resource.start()
        try:
            return self._finalize(run_id, context, stop_reason, cancel)
        finally:
            resource.close()

    def _finalize(
        self,
        run_id: str,
        context: tuple[ModelContextItem, ...],
        stop_reason: str,
        cancel: threading.Event,
    ) -> FinalizationOutcome:
        self.state_machine.track(RuntimeState.FINALIZING, stop_reason)
        current_run = self.store.read_run(run_id)
        started = self.store.begin_finalization_attempt_committed(
            run_id, model_id=str(current_run["modelId"])
        )
        attempt, finalizing_run = started.value
        self.events.publish(started, run=finalizing_run)
        timed_out = threading.Event()
        request_cancel = _CombinedCancellation(cancel, timed_out)
        timer = threading.Timer(self.timeout_seconds, timed_out.set)
        timer.start()
        failure_reason: str | None = None
        canceled = False
        item: dict[str, object] | None = None
        writer = AssistantStreamWriter(
            self.store,
            self.events,
            run_id,
            None,
            check_cancel=lambda: _raise_finalization_cancel(request_cancel),
        )

        try:
            hit_fault("finalization_model_failure")
            ModelRunner(self.model, self.sensitive).run(
                (*context, {
                    "type": "finalization",
                    "toolsAllowed": False,
                    "stopReason": stop_reason,
                }),
                request_cancel,
                writer.write,
                allow_tools=False,
            )
            if cancel.is_set():
                raise RuntimeCancelled
            if timed_out.is_set():
                failure_reason = "finalization_timeout"
            else:
                writer.flush()
        except SensitiveScanError:
            failure_reason = "finalization_sensitive_content_rejected"
        except ModelStreamInterrupted as error:
            if cancel.is_set():
                canceled = True
            else:
                failure_reason = (
                    "finalization_timeout"
                    if timed_out.is_set()
                    else "finalization_sensitive_content_rejected"
                    if isinstance(error.cause, SensitiveScanError)
                    else "finalization_model_failed"
                )
        except RuntimeCancelled:
            if cancel.is_set():
                canceled = True
            else:
                failure_reason = "finalization_timeout"
        except Exception as error:
            failure_reason = "finalization_model_failed"
            logger.warning("Finalization failed: %s", type(error).__name__)
        finally:
            timer.cancel()

        if (failure_reason is not None or canceled or cancel.is_set()) and writer.item is not None:
            item = writer.abort()
        if canceled or cancel.is_set():
            raise RuntimeCancelled
        if failure_reason is not None:
            logger.warning("Finalization ended without an item: %s", failure_reason)
        hit_fault("cancel_finalization_race")
        try:
            mutation = self.store.complete_finalization_and_stop_committed(
                str(writer.item["id"])
                if failure_reason is None and writer.item is not None
                else None,
                run_id,
                stop_reason,
                attempt_id=str(attempt["id"]),
                attempt_status=_attempt_status(failure_reason),
                error_code=failure_reason,
            )
        except InvalidRunStateError:
            current = self.store.read_run(run_id)
            if current["status"] == "canceled":
                writer.abort()
                raise RuntimeCancelled from None
            if current["status"] == "stopped":
                item = (
                    self.store.read_item(str(writer.item["id"]))
                    if writer.item is not None else None
                )
                return FinalizationOutcome(
                    run=current, item=item, failure_reason=failure_reason
                )
            raise
        completed_item, stopped = mutation.value
        self.events.publish(mutation, run=stopped, item=completed_item)
        if completed_item is not None:
            item = completed_item
        self.state_machine.track(RuntimeState.COMPLETED, "finalization_stopped")
        return FinalizationOutcome(
            run=stopped, item=item, failure_reason=failure_reason
        )


def _raise_finalization_cancel(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise RuntimeCancelled


def _attempt_status(failure_reason: str | None) -> str:
    return {
        None: "completed",
        "finalization_timeout": "timed_out",
        "finalization_sensitive_content_rejected": "sensitive_rejected",
    }.get(failure_reason, "model_failed")
