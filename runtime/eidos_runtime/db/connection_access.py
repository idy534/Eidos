"""Access paths around the single authoritative ``state.sqlite`` writer.

``Database.lock`` serializes every use of the shared write connection. Two
helpers here keep that authority intact while removing its side effects on
unrelated readers:

* ``MonitoredRLock`` keeps the exact ``RLock`` semantics and reports when one
  thread waits for or holds the lock long enough to stall the protocol.
* ``ReadConnectionPool`` gives side-effect-free readers short read-only WAL
  transactions. They see committed state only and never wait for the writer
  lock, so a long writer or scan cannot stall a Session switch.
"""

from __future__ import annotations

from contextlib import contextmanager
import logging
from pathlib import Path
import sqlite3
import stat
import threading
import time
from typing import Callable, Iterator


logger = logging.getLogger("eidos.runtime.storage")

LOCK_REPORT_SECONDS = 1.0
READ_POOL_SIZE = 4
READ_BUSY_TIMEOUT_MS = 5000


class MonitoredRLock:
    """Reentrant lock that logs outermost holds or waits above a threshold.

    The log carries the thread name, so a stall can be attributed to the task
    that held the State lock instead of being inferred from slow requests.

    ``bypass`` lets a thread inside a read-only snapshot scope pass lock
    regions without waiting: it does not touch the shared write connection.
    """

    def __init__(
        self,
        *,
        name: str,
        report_seconds: float = LOCK_REPORT_SECONDS,
        bypass: Callable[[], bool] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._name = name
        self._report_seconds = report_seconds
        # Startup migrations and integrity checks hold the lock by design.
        self.reporting = True
        self._bypass = bypass
        self._bypassed = threading.local()
        self._owner: int | None = None
        self._depth = 0
        self._acquired_at = 0.0

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        identity = threading.get_ident()
        if self._owner != identity and self._bypass is not None and self._bypass():
            self._bypassed.depth = getattr(self._bypassed, "depth", 0) + 1
            return True
        if self._owner == identity:
            acquired = self._lock.acquire(blocking, timeout)
            if acquired:
                self._depth += 1
            return acquired
        started = time.monotonic()
        acquired = self._lock.acquire(blocking, timeout)
        if not acquired:
            return False
        now = time.monotonic()
        self._owner = identity
        self._depth = 1
        self._acquired_at = now
        waited = now - started
        if self.reporting and waited >= self._report_seconds:
            logger.warning(
                "storage_lock_wait lock=%s wait_ms=%.0f thread=%s",
                self._name,
                waited * 1000,
                threading.current_thread().name,
            )
        return True

    def release(self) -> None:
        if self._owner != threading.get_ident():
            bypassed = getattr(self._bypassed, "depth", 0)
            if bypassed:
                self._bypassed.depth = bypassed - 1
                return
            # Preserve RLock's own error for a foreign or unowned release.
            self._lock.release()
            return
        if self._depth == 1:
            held = time.monotonic() - self._acquired_at
            self._owner = None
            self._depth = 0
            self._lock.release()
            if self.reporting and held >= self._report_seconds:
                logger.warning(
                    "storage_lock_held lock=%s hold_ms=%.0f thread=%s",
                    self._name,
                    held * 1000,
                    threading.current_thread().name,
                )
            return
        self._depth -= 1
        self._lock.release()

    def _is_owned(self) -> bool:
        """Mirror ``RLock._is_owned``; a snapshot scope owns its own connection."""

        return self._owner == threading.get_ident() or bool(
            getattr(self._bypassed, "depth", 0)
        )

    def held_by_current_thread(self) -> bool:
        return self._owner == threading.get_ident()

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *_exc: object) -> None:
        self.release()


class ReadConnectionPool:
    """Bounded read-only connections to one verified private SQLite file."""

    def __init__(self, path: Path, *, size: int = READ_POOL_SIZE) -> None:
        if size < 1:
            raise ValueError("read pool size must be positive")
        metadata = path.lstat()
        self._path = path
        self._identity = (metadata.st_dev, metadata.st_ino)
        self._size = size
        self._idle: list[sqlite3.Connection] = []
        self._lock = threading.Lock()
        self._closed = False

    @contextmanager
    def try_transaction(self) -> Iterator[sqlite3.Connection | None]:
        """Like ``transaction``, but yield ``None`` when no connection opens.

        Callers then fall back to the shared writer connection under its lock,
        so a read-only open failure costs latency instead of failing the read.
        """

        try:
            connection = self._take()
        except (OSError, sqlite3.Error) as error:
            logger.warning(
                "storage_read_connection_unavailable error_class=%s", type(error).__name__
            )
            yield None
            return
        with self._transaction(connection) as active:
            yield active

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Yield one read transaction; every statement sees the same snapshot."""

        with self._transaction(self._take()) as connection:
            yield connection

    @contextmanager
    def _transaction(self, connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
        reusable = False
        try:
            connection.execute("BEGIN")
            yield connection
            if connection.in_transaction:
                connection.execute("COMMIT")
            reusable = True
        finally:
            if not reusable:
                try:
                    if connection.in_transaction:
                        connection.execute("ROLLBACK")
                    reusable = True
                except sqlite3.Error:
                    reusable = False
            self._give_back(connection, reusable)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            idle, self._idle = self._idle, []
        for connection in idle:
            connection.close()

    def _take(self) -> sqlite3.Connection:
        with self._lock:
            if self._closed:
                raise sqlite3.ProgrammingError("read pool is closed")
            if self._idle:
                return self._idle.pop()
        return self._open()

    def _give_back(self, connection: sqlite3.Connection, reusable: bool) -> None:
        with self._lock:
            if reusable and not self._closed and len(self._idle) < self._size:
                self._idle.append(connection)
                return
        connection.close()

    def _open(self) -> sqlite3.Connection:
        metadata = self._path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or (metadata.st_dev, metadata.st_ino) != self._identity
        ):
            raise sqlite3.OperationalError("state database identity changed")
        connection = sqlite3.connect(
            self._path.as_uri() + "?mode=ro",
            uri=True,
            check_same_thread=False,
            isolation_level=None,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout = {READ_BUSY_TIMEOUT_MS}")
            connection.execute("PRAGMA query_only = ON")
        except BaseException:
            connection.close()
            raise
        return connection


__all__ = ["MonitoredRLock", "ReadConnectionPool"]
