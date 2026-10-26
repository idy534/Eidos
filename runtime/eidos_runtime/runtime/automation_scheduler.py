from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
import logging
import threading
import time

import anyio

from eidos_runtime.domain.automation import AutomationError
from eidos_runtime.infrastructure.automation_time import future_times
from eidos_runtime.persistence.automations import AutomationRepository

logger = logging.getLogger(__name__)
MAX_WAIT_SECONDS = 300.0
CLOCK_TOLERANCE_SECONDS = 30.0


class AutomationScheduler:
    """Future-only timer cache. SQLite owns claims and Run admission."""

    def __init__(self, repository: AutomationRepository, launch: Callable[[str], None],
                 enabled: Callable[[], bool], publish: Callable[[], object], *,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self.repository = repository
        self.launch = launch
        self.enabled = enabled
        self.publish = publish
        self.clock = clock
        self.monotonic = monotonic
        self._next: dict[str, tuple[int, datetime]] = {}
        self._reset = threading.Event()
        self._last: datetime | None = None
        self._last_monotonic: float | None = None
        self._wake_lock = threading.Lock()
        self._wake: tuple[anyio.Event, asyncio.AbstractEventLoop] | None = None

    def refresh(self) -> None:
        with self._wake_lock:
            self._reset.set()
        self.wake()

    def wake(self) -> None:
        with self._wake_lock:
            wake = self._wake
        if wake is None:
            return
        event, loop = wake
        # Post to RuntimeAsyncKernel's existing asyncio backend without blocking
        # the writer. AnyIO continues to own the wait and cancellation.
        try:
            loop.call_soon_threadsafe(event.set)
        except RuntimeError:
            if not loop.is_closed():
                raise

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        expected_wait = 0.0
        try:
            await anyio.to_thread.run_sync(self.repository.recover)
            while True:
                event = anyio.Event()
                # Register before reading SQLite so changes during a scan cannot be lost.
                with self._wake_lock:
                    self._wake = (event, loop)
                try:
                    await anyio.to_thread.run_sync(lambda: self.tick(self.clock(), expected_wait=expected_wait))
                    delay = self.next_delay(self.clock())
                except Exception:
                    logger.exception("Automation scheduling failed")
                    delay = 5.0
                expected_wait = delay
                with anyio.move_on_after(delay):
                    await event.wait()
        finally:
            with self._wake_lock:
                self._wake = None

    def next_delay(self, now: datetime) -> float:
        if not self._next:
            return MAX_WAIT_SECONDS
        due = min(value[1] for value in self._next.values())
        return min(MAX_WAIT_SECONDS, max(0.0, (due - now).total_seconds()))

    def tick(self, now: datetime, *, expected_wait: float = 0.0) -> None:
        if not self.enabled():
            self._reset.set()
            self._next.clear()
            return
        steady_now = self.monotonic()
        clock_changed = False
        if self._last is not None and self._last_monotonic is not None:
            elapsed = (now - self._last).total_seconds()
            steady_elapsed = steady_now - self._last_monotonic
            clock_changed = (elapsed < 0 or elapsed > expected_wait + CLOCK_TOLERANCE_SECONDS
                or abs(elapsed - steady_elapsed) > CLOCK_TOLERANCE_SECONDS)
        with self._wake_lock:
            reset = self._reset.is_set()
            self._reset.clear()
        if reset or clock_changed:
            self._next.clear()
        self._last = now
        self._last_monotonic = steady_now
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
