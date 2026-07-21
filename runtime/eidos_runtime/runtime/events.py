from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Callable

from eidos_runtime.db.storage import CommittedMutation
from eidos_runtime.runtime.event_projector import EventProjector


logger = logging.getLogger("eidos.runtime")


class RuntimeOutputClosedError(OSError):
    """The Runtime notification output is no longer writable."""


@dataclass(frozen=True)
class DeliveryFailure:
    event_id: int | None
    method: str | None
    error_type: str


@dataclass(frozen=True)
class DeliveryResult:
    attempted: int = 0
    delivered: int = 0
    failures: tuple[DeliveryFailure, ...] = ()


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
    ) -> DeliveryResult:
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
