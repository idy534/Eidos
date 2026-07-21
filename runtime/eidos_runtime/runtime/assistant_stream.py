from __future__ import annotations

import time
from typing import Callable

from eidos_runtime.db.storage import InvalidRunStateError, SessionStore
from eidos_runtime.runtime.events import RuntimeEvents


MAX_ASSISTANT_BYTES = 512 * 1024
DELTA_BATCH_BYTES = 4 * 1024
DELTA_BATCH_SECONDS = 0.1


class AssistantStreamTooLarge(RuntimeError):
    pass


class AssistantStreamWriter:
    """Persists one streamed Assistant Item from already-safe text deltas."""

    def __init__(
        self,
        store: SessionStore,
        events: RuntimeEvents,
        run_id: str,
        model_step_index: int | None,
        *,
        check_cancel: Callable[[], None] = lambda: None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self.events = events
        self.run_id = run_id
        self.model_step_index = model_step_index
        self.check_cancel = check_cancel
        self.monotonic = monotonic
        self.item: dict[str, object] | None = None
        self._bytes = 0
        self._sequence = 0
        self._pending: list[str] = []
        self._pending_bytes = 0
        self._last_flush = monotonic()

    def write(self, delta: str) -> None:
        self.check_cancel()
        if not isinstance(delta, str) or not delta:
            return
        size = len(delta.encode("utf-8"))
        self._bytes += size
        if self._bytes > MAX_ASSISTANT_BYTES:
            raise AssistantStreamTooLarge("assistant output is too large")
        if self.item is None:
            mutation = (
                self.store.create_finalization_assistant_item_committed(self.run_id)
                if self.model_step_index is None
                else self.store.create_assistant_item_committed(
                    self.run_id, self.model_step_index
                )
            )
            self.item = mutation.value
            self.events.publish(mutation, item=self.item)
        self._sequence += 1
        self._pending.append(delta)
        self._pending_bytes += size
        if (
            self._pending_bytes >= DELTA_BATCH_BYTES
            or self.monotonic() - self._last_flush >= DELTA_BATCH_SECONDS
        ):
            self.flush()

    def flush(self) -> None:
        if self.item is None or not self._pending:
            return
        mutation = self.store.append_item_deltas_committed(
            str(self.item["id"]),
            tuple(self._pending),
            self._sequence - len(self._pending) + 1,
        )
        self.item = mutation.value
        self.events.publish(mutation, item=self.item)
        self._pending.clear()
        self._pending_bytes = 0
        self._last_flush = self.monotonic()

    def complete(self) -> dict[str, object] | None:
        self.flush()
        if self.item is None:
            return None
        mutation = self.store.complete_assistant_item_committed(str(self.item["id"]))
        self.item = mutation.value
        self.events.publish(mutation, item=self.item)
        return self.item

    def abort(self) -> dict[str, object] | None:
        if self.item is None:
            return None
        try:
            self.flush()
        except InvalidRunStateError:
            self._pending.clear()
            self._pending_bytes = 0
        mutation = self.store.mark_assistant_incomplete_if_active_committed(
            str(self.item["id"])
        )
        if mutation is None:
            self.item = self.store.read_item(str(self.item["id"]))
            return None
        self.item = mutation.value
        self.events.publish(mutation, item=self.item)
        return self.item
