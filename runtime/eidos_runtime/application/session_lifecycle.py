from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock


@dataclass
class _LockEntry:
    lock: Lock = field(default_factory=Lock)
    users: int = 0


class SessionLifecycleCoordinator:
    """Serialize Run admission and Session deletion for one Session.

    The coordinator owns no durable state. SQLite remains the final authority
    for active Runs and Session existence. The keyed lock only closes the gap
    around Git observations that must happen outside a SQLite transaction.
    """

    def __init__(self) -> None:
        self._guard = Lock()
        self._locks: dict[str, _LockEntry] = {}

    @contextmanager
    def hold(self, session_id: str) -> Iterator[None]:
        with self._hold_key(f"session\0{session_id}"):
            yield

    @contextmanager
    def hold_workspace(self, workspace_root: str | Path) -> Iterator[None]:
        with self.hold_workspaces((workspace_root,)):
            yield

    @contextmanager
    def hold_workspaces(self, workspace_roots: Iterable[str | Path]) -> Iterator[None]:
        keys = {Path(root).resolve(strict=False).as_posix() for root in workspace_roots}
        with ExitStack() as locks:
            for key in sorted(keys):
                locks.enter_context(self._hold_key(f"workspace\0{key}"))
            yield

    @contextmanager
    def hold_operation(self, scope: str, operation_id: str) -> Iterator[None]:
        with self._hold_key(f"operation\0{scope}\0{operation_id}"):
            yield

    @contextmanager
    def _hold_key(self, key: str) -> Iterator[None]:
        with self._guard:
            entry = self._locks.get(key)
            if entry is None:
                entry = _LockEntry()
                self._locks[key] = entry
            entry.users += 1
        entry.lock.acquire()
        try:
            yield
        finally:
            entry.lock.release()
            with self._guard:
                entry.users -= 1
                if entry.users == 0:
                    self._locks.pop(key, None)


__all__ = ["SessionLifecycleCoordinator"]
