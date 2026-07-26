from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Callable

from pydantic import BaseModel, ConfigDict

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.runtime.event_projector import EventProjector


logger = logging.getLogger("eidos.runtime")


class EventOutboxError(RuntimeError):
    pass


class RuntimeOutputClosedError(OSError):
    """The Runtime notification output is no longer writable."""


class EventOutboxEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    event_id: int
    status: str
    attempt_count: int
    last_error_code: str | None = None
    delivered_at: int | None = None


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


class EventDelivery:
    def __init__(
        self,
        store: SessionStore,
        notify: Callable[[dict[str, object]], None],
        projector: EventProjector | None = None,
    ) -> None:
        self.store = store
        self.notify = notify
        self.projector = projector or EventProjector()

    def deliver(
        self,
        *,
        through_event_id: int | None = None,
        runs: dict[int, dict[str, object]] | None = None,
        items: dict[int, dict[str, object]] | None = None,
    ) -> DeliveryResult:
        attempted = 0
        delivered = 0
        failures: list[DeliveryFailure] = []
        for event in self.store.pending_outbox_events(
            through_event_id=through_event_id
        ):
            event_id = event.get("eventId")
            if not isinstance(event_id, int):
                raise EventOutboxError("event id is invalid")
            try:
                run = runs.get(event_id) if runs is not None else None
                item = items.get(event_id) if items is not None else None
                run, item = self._context(event, run=run, item=item)
                notifications = self.projector.project(
                    event, run=run, item=item
                )
            except Exception as error:
                self.store.record_outbox_failure(
                    event_id, "EVENT_PROJECTION_FAILED"
                )
                logger.warning(
                    "Event projection failed: %s",
                    type(error).__name__,
                )
                return DeliveryResult(
                    failures=(
                        DeliveryFailure(
                            event_id,
                            None,
                            type(error).__name__,
                        ),
                    )
                )
            event_delivered = 0
            for notification in notifications:
                attempted += 1
                try:
                    self.notify(notification)
                    event_delivered += 1
                except (
                    BrokenPipeError,
                    ConnectionError,
                    RuntimeOutputClosedError,
                    OSError,
                ) as error:
                    failure = DeliveryFailure(
                        event_id=event_id,
                        method=(
                            str(notification["method"])
                            if isinstance(
                                notification.get("method"), str
                            )
                            else None
                        ),
                        error_type=type(error).__name__,
                    )
                    failures.append(failure)
                    self.store.record_outbox_failure(
                        event_id, "EVENT_DELIVERY_FAILED"
                    )
                    logger.warning(
                        "Notification delivery failed",
                        extra={
                            "event_id": event_id,
                            "notification_method": failure.method,
                            "delivery_error_type": failure.error_type,
                        },
                    )
                    return DeliveryResult(
                        attempted=attempted,
                        delivered=delivered + event_delivered,
                        failures=tuple(failures),
                    )
            self.store.mark_outbox_delivered(event_id)
            delivered += event_delivered
        return DeliveryResult(
            attempted=attempted,
            delivered=delivered,
            failures=tuple(failures),
        )

    def _context(
        self,
        event: dict[str, object],
        *,
        run: dict[str, object] | None,
        item: dict[str, object] | None,
    ) -> tuple[
        dict[str, object] | None,
        dict[str, object] | None,
    ]:
        run_id = event.get("runId")
        if run is None and isinstance(run_id, str):
            run = self.store.read_run(run_id)
        payload = event.get("payload")
        if item is None and isinstance(payload, dict):
            item_id = payload.get("itemId")
            if isinstance(item_id, str):
                item = self.store.read_item(item_id)
        return run, item
