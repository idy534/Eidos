"""Supervisor-owned storage maintenance. This task never invokes a model."""

from __future__ import annotations

from collections.abc import Callable
import logging
import threading
import time

from eidos_runtime.memory.service import MemoryService

logger = logging.getLogger("eidos.runtime.storage")


class StorageMaintenance:
    def __init__(
        self,
        memory: MemoryService,
        busy: Callable[[], bool],
        idle: Callable[[threading.Event], None],
    ) -> None:
        self.memory, self.busy, self.idle = memory, busy, idle

    def run(self, cancel: threading.Event) -> None:
        collection = time.monotonic() + 60
        while not cancel.is_set():
            try:
                self.memory.cleanup()
                if time.monotonic() >= collection and not self.busy():
                    self.memory.collect_unreferenced_files()
                    self.idle(cancel)
                    collection = time.monotonic() + 3600
            except Exception as error:
                logger.warning(
                    "Storage maintenance deferred error_class=%s", type(error).__name__
                )
                collection = time.monotonic() + 3600
            cancel.wait(1)
