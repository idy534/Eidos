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
    ) -> None:
        self.store = store
        self.model = model
        self.events = events
        self.sensitive = sensitive
        self.state_machine = state_machine
        self.timeout_seconds = timeout_seconds

    def finalize(
        self,
        run_id: str,
        context: tuple[ModelContextItem, ...],
        stop_reason: str,
        cancel: threading.Event,
    ) -> FinalizationOutcome:
        self.state_machine.track(RuntimeState.FINALIZING, stop_reason)
        finalizing = self.store.begin_finalization_committed(run_id)
        self.events.publish(finalizing, run=finalizing.value)
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
            ModelRunner(self.model, self.sensitive).run(
                (*context, {"type": "finalization", "toolsAllowed": False}),
                request_cancel,
                writer.write,
                allow_tools=False,
            )
            if cancel.is_set():
                raise RuntimeCancelled
            if timed_out.is_set():
                failure_reason = "finalization_timeout"
            else:
                item = writer.complete()
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
            try:
                item = writer.fail()
            except InvalidRunStateError:
                if self.store.read_run(run_id)["status"] != "canceled":
                    raise
        if canceled or cancel.is_set():
            raise RuntimeCancelled
        if failure_reason is not None:
            logger.warning("Finalization ended without an item: %s", failure_reason)
        try:
            mutation = self.store.stop_run_committed(run_id, stop_reason)
        except InvalidRunStateError:
            if cancel.is_set() or self.store.read_run(run_id)["status"] == "canceled":
                raise RuntimeCancelled from None
            raise
        stopped = mutation.value
        self.events.publish(mutation, run=stopped)
        self.state_machine.track(RuntimeState.COMPLETED, "finalization_stopped")
        return FinalizationOutcome(
            run=stopped, item=item, failure_reason=failure_reason
        )


def _raise_finalization_cancel(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise RuntimeCancelled
