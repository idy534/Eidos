from __future__ import annotations

from contextlib import contextmanager
import threading
from typing import Iterator, Protocol


class FaultInjector(Protocol):
    def hit(self, point: str) -> None: ...


class NoopFaultInjector:
    def hit(self, _point: str) -> None:
        return


_lock = threading.RLock()
_injector: FaultInjector = NoopFaultInjector()


def hit_fault(point: str) -> None:
    with _lock:
        injector = _injector
    injector.hit(point)


@contextmanager
def injected_faults(injector: FaultInjector) -> Iterator[None]:
    global _injector
    with _lock:
        previous = _injector
        _injector = injector
    try:
        yield
    finally:
        with _lock:
            _injector = previous
