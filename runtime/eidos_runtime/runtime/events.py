from __future__ import annotations

from typing import Callable

from eidos_runtime.db.storage import CommittedMutation
from eidos_runtime.runtime.event_projector import EventProjector


class RuntimeEvents:
    """Publishes protocol notifications only from committed Event envelopes."""

    def __init__(
        self,
        notify: Callable[[dict[str, object]], None],
        projector: EventProjector | None = None,
    ) -> None:
        self._notify = notify
        self._projector = projector or EventProjector()

    def publish(
        self,
        mutation: CommittedMutation[object],
        *,
        run: dict[str, object] | None = None,
        item: dict[str, object] | None = None,
        items: dict[str, dict[str, object]] | None = None,
    ) -> None:
        for event in mutation.events:
            event_item = item
            payload = event.get("payload")
            if items is not None and isinstance(payload, dict):
                item_id = payload.get("itemId")
                if isinstance(item_id, str):
                    event_item = items.get(item_id)
            self.publish_event(event, run=run, item=event_item)

    def publish_event(
        self,
        event: dict[str, object],
        *,
        run: dict[str, object] | None = None,
        item: dict[str, object] | None = None,
    ) -> None:
        for notification in self._projector.project(event, run=run, item=item):
            self._notify(notification)
