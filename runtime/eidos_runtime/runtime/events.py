from __future__ import annotations

import logging
from typing import Callable

from eidos_runtime.db.storage import CommittedMutation
from eidos_runtime.runtime.event_projector import EventProjector
from eidos_runtime.runtime.event_delivery import (
    DeliveryFailure,
    DeliveryResult,
    EventDelivery,
    RuntimeOutputClosedError,
)


logger = logging.getLogger("eidos.runtime")


class RuntimeEvents:
    """Publishes protocol notifications only from committed Event envelopes."""

    def __init__(
        self,
        notify: Callable[[dict[str, object]], None],
        projector: EventProjector | None = None,
        store=None,
    ) -> None:
        self._notify = notify
        self._projector = projector or EventProjector()
        self._delivery = (
            EventDelivery(store, notify, self._projector)
            if store is not None
            else None
        )

    def publish(
        self,
        mutation: CommittedMutation[object],
        *,
        run: dict[str, object] | None = None,
        item: dict[str, object] | None = None,
        items: dict[str, dict[str, object]] | None = None,
    ) -> DeliveryResult:
        if self._delivery is not None:
            event_ids = mutation.event_ids
            if not event_ids:
                return DeliveryResult()
            run_context = (
                {event_id: run for event_id in event_ids}
                if run is not None
                else None
            )
            item_context: dict[int, dict[str, object]] = {}
            if item is not None:
                item_context.update(
                    (event_id, item) for event_id in event_ids
                )
            if items is not None:
                for event in mutation.events:
                    event_id = event.get("eventId")
                    payload = event.get("payload")
                    if (
                        isinstance(event_id, int)
                        and isinstance(payload, dict)
                        and isinstance(payload.get("itemId"), str)
                        and payload["itemId"] in items
                    ):
                        item_context[event_id] = items[payload["itemId"]]
            return self._delivery.deliver(
                through_event_id=max(event_ids),
                runs=run_context,
                items=item_context or None,
            )
        results: list[DeliveryResult] = []
        for event in mutation.events:
            event_item = item
            payload = event.get("payload")
            if items is not None and isinstance(payload, dict):
                item_id = payload.get("itemId")
                if isinstance(item_id, str):
                    event_item = items.get(item_id)
            results.append(self.publish_event(event, run=run, item=event_item))
        return DeliveryResult(
            attempted=sum(result.attempted for result in results),
            delivered=sum(result.delivered for result in results),
            failures=tuple(
                failure for result in results for failure in result.failures
            ),
        )

    def publish_event(
        self,
        event: dict[str, object],
        *,
        run: dict[str, object] | None = None,
        item: dict[str, object] | None = None,
    ) -> DeliveryResult:
        if self._delivery is not None:
            event_id = event.get("eventId")
            if not isinstance(event_id, int):
                return DeliveryResult()
            return self._delivery.deliver(
                through_event_id=event_id,
                runs={event_id: run} if run is not None else None,
                items={event_id: item} if item is not None else None,
            )
        notifications = self._projector.project(event, run=run, item=item)
        delivered = 0
        failures: list[DeliveryFailure] = []
        for notification in notifications:
            try:
                self._notify(notification)
                delivered += 1
            except (
                BrokenPipeError,
                ConnectionError,
                RuntimeOutputClosedError,
                OSError,
            ) as error:
                failure = DeliveryFailure(
                    event_id=(
                        event.get("eventId")
                        if isinstance(event.get("eventId"), int)
                        else None
                    ),
                    method=(
                        str(notification["method"])
                        if isinstance(notification.get("method"), str)
                        else None
                    ),
                    error_type=type(error).__name__,
                )
                failures.append(failure)
                logger.warning(
                    "Notification delivery failed",
                    extra={
                        "event_id": failure.event_id,
                        "notification_method": failure.method,
                        "delivery_error_type": failure.error_type,
                    },
                )
        return DeliveryResult(
            attempted=len(notifications),
            delivered=delivered,
            failures=tuple(failures),
        )

    def deliver_pending(self) -> DeliveryResult:
        if self._delivery is None:
            return DeliveryResult()
        return self._delivery.deliver()
