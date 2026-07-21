from __future__ import annotations

import logging
import threading

from pydantic import BaseModel, ConfigDict

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import ModelClient, ModelContextItem, ModelResponse
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.runtime.state_machine import RuntimePhaseTracker, RuntimeState
from eidos_runtime.sandbox.sensitive import (
    SensitiveScanError,
    SensitiveScanner,
    StreamingSensitiveScanner,
)


logger = logging.getLogger("eidos.runtime")
FINALIZATION_SECONDS = 60
MAX_ASSISTANT_BYTES = 512 * 1024


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
    ) -> FinalizationOutcome:
        self.state_machine.track(RuntimeState.FINALIZING, stop_reason)
        finalizing = self.store.begin_finalization_committed(run_id)
        self.events.publish(finalizing, run=finalizing.value)
        timed_out = threading.Event()
        timer = threading.Timer(self.timeout_seconds, timed_out.set)
        timer.start()
        stream = StreamingSensitiveScanner(self.sensitive)
        streamed = False
        total_bytes = 0
        failure_reason: str | None = None
        item: dict[str, object] | None = None

        def on_delta(delta: str) -> None:
            nonlocal streamed, total_bytes
            if timed_out.is_set() or not delta:
                return
            streamed = True
            total_bytes += len(delta.encode("utf-8"))
            if total_bytes > MAX_ASSISTANT_BYTES:
                timed_out.set()
                return
            stream.feed(delta)

        try:
            response = self.model.complete(
                (*context, {"type": "finalization", "toolsAllowed": False}),
                timed_out,
                on_delta,
                allow_tools=False,
            )
            if isinstance(response, ModelResponse) and response.text and not streamed:
                on_delta(response.text)
            if timed_out.is_set():
                failure_reason = "finalization_timeout"
            else:
                safe_text = stream.finish().text
                if safe_text:
                    item = self.store.create_finalization_assistant_item(run_id)
                    self.store.append_item_content(str(item["id"]), safe_text)
                    mutation = self.store.complete_assistant_item_committed(
                        str(item["id"])
                    )
                    item = mutation.value
                    self.events.publish(mutation, item=item)
        except SensitiveScanError:
            failure_reason = "finalization_sensitive_content_rejected"
        except Exception as error:
            failure_reason = "finalization_model_failed"
            logger.warning("Finalization failed: %s", type(error).__name__)
        finally:
            timer.cancel()

        if failure_reason is not None:
            logger.warning("Finalization ended without an item: %s", failure_reason)
        mutation = self.store.stop_run_committed(run_id, stop_reason)
        stopped = mutation.value
        self.events.publish(mutation, run=stopped)
        self.state_machine.track(RuntimeState.COMPLETED, "finalization_stopped")
        return FinalizationOutcome(
            run=stopped, item=item, failure_reason=failure_reason
        )
