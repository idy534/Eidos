from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import logging
import threading

import anyio

from eidos_runtime.domain.automation import AutomationError
from eidos_runtime.infrastructure.automation_time import future_times
from eidos_runtime.persistence.automations import AutomationRepository

logger = logging.getLogger(__name__)


class AutomationScheduler:
    """Future-only timer cache. SQLite owns claims and Run admission."""

    def __init__(self, repository: AutomationRepository, launch: Callable[[str], None],
                 enabled: Callable[[], bool], publish: Callable[[], object]) -> None:
        self.repository = repository
        self.launch = launch
        self.enabled = enabled
        self.publish = publish
        self._next: dict[str, tuple[int, datetime]] = {}
        self._reset = threading.Event()
        self._last: datetime | None = None

    def refresh(self) -> None:
        self._reset.set()

    async def run(self) -> None:
        self.repository.recover()
        while True:
            try:
                await anyio.to_thread.run_sync(self.tick, datetime.now(timezone.utc))
            except Exception:
                logger.exception("Automation scheduling failed")
            await anyio.sleep(5)

    def tick(self, now: datetime) -> None:
        if not self.enabled():
            self._reset.set()
            return
        if self._reset.is_set() or (self._last and (now < self._last or (now - self._last).total_seconds() > 30)):
            self._next.clear()
            self._reset.clear()
        self._last = now
        after = 0
        seen: set[str] = set()
        while True:
            rows = self.repository.list(after, 100)
            for seq, task in rows:
                after = seq
                seen.add(task.id)
                if task.deleted_at is not None or task.status != "ACTIVE":
                    self._next.pop(task.id, None)
                    continue
                cached = self._next.get(task.id)
                if cached is None or cached[0] != task.revision:
                    times = future_times(task.kind, task.schedule, now, 1)
                    if not times:
                        self.repository.pause_expired(task)
                        continue
                    self._next[task.id] = (task.revision, datetime.fromisoformat(times[0]))
                    continue
                due = cached[1]
                if now >= due:
                    try:
                        if (now - due).total_seconds() < 60:
                            occurrence = self.repository.claim(task.id, due.isoformat())
                            if occurrence and occurrence.status == "preparing":
                                self.launch(occurrence.id)
                    except AutomationError:
                        logger.exception("Automation could not be accepted: %s", task.id)
                    self._next.pop(task.id, None)
            if len(rows) < 100:
                break
        self._next = {key: value for key, value in self._next.items() if key in seen}
        self.publish()
