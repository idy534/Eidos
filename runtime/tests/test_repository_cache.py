from __future__ import annotations

from pathlib import Path
import sqlite3
import threading

import pytest

from eidos_runtime.db import layout
from eidos_runtime.db.layout import RepositoryDatabase
from eidos_runtime.db.schema import REPOSITORY_SCHEMA_SQL
from eidos_runtime.db.storage import SessionStore


pytestmark = pytest.mark.integration


def test_only_repository_skips_startup_full_checks_and_vacuum(tmp_path: Path, monkeypatch) -> None:
    statements: list[tuple[str, str]] = []
    connect = sqlite3.connect

    def traced(path, *args, **kwargs):
        connection = connect(path, *args, **kwargs)
        connection.set_trace_callback(lambda sql: statements.append((Path(path).name, sql)))
        return connection

    monkeypatch.setattr(sqlite3, "connect", traced)
    store = SessionStore(tmp_path / "data")
    try:
        store.initialize()
        assert store.health_state == "ready"
        assert ("state.sqlite", "PRAGMA integrity_check") in statements
        assert ("thread_history.sqlite", "PRAGMA integrity_check") in statements
        assert not any(name == "repository.sqlite" and sql in {
            "PRAGMA integrity_check", "PRAGMA foreign_key_check", "VACUUM",
        } for name, sql in statements)
    finally:
        store.close()


def test_old_repository_never_forces_full_vacuum_conversion(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    path = data / "repository.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript(REPOSITORY_SCHEMA_SQL)
        connection.execute("PRAGMA user_version = 1")
    path.chmod(0o600)
    database = RepositoryDatabase(data)
    try:
        database.initialize()
        assert database.connection().execute("PRAGMA auto_vacuum").fetchone()[0] == 0
        assert database.maintain(threading.Event()) is False
        assert database.connection().execute("PRAGMA auto_vacuum").fetchone()[0] == 0
    finally:
        database.close()


def test_corrupt_repository_header_rebuild_preserves_authoritative_sessions(tmp_path: Path) -> None:
    data = tmp_path / "data"
    store = SessionStore(data)
    store.initialize()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = store.create_session(str(workspace))
    store.close()
    path = data / "repository.sqlite"
    damaged = b"invalid SQLite cache header"
    path.write_bytes(damaged)
    reopened = SessionStore(data)
    try:
        reopened.initialize()
        assert reopened.health_state == "ready"
        assert reopened.read_session(session["id"])["id"] == session["id"]
        assert reopened.repository_database.connection().execute("SELECT COUNT(*) FROM repository_snapshots").fetchone()[0] == 0
        preserved = tuple(data.glob("repository.sqlite.invalid-*"))
        assert len(preserved) == 1
        assert preserved[0].read_bytes() == damaged
    finally:
        reopened.close()


def test_idle_integrity_failure_discards_only_repository_cache(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        repository = store.repository_database
        connection = repository.connection()
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.executescript("CREATE TABLE cache_parent(id PRIMARY KEY); CREATE TABLE cache_child(parent REFERENCES cache_parent(id)); INSERT INTO cache_child VALUES (1);")
        connection.execute("PRAGMA foreign_keys = ON")
        assert store.maintain_repository_cache(threading.Event()) is True
        assert store.health_state == "ready"
        assert not repository.connection().execute("SELECT name FROM sqlite_master WHERE name = 'cache_child'").fetchall()
        assert len(tuple(store.data_directory.glob("repository.sqlite.invalid-*"))) == 1
    finally:
        store.close()


def test_cancel_interrupts_repository_check_without_rebuilding(tmp_path: Path, monkeypatch) -> None:
    database = RepositoryDatabase(tmp_path / "data")
    database.initialize()
    before = database.path.stat().st_ino
    cancel = threading.Event()

    def long_check(connection):
        cancel.set()
        connection.execute("WITH RECURSIVE seq(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM seq WHERE x<100000) SELECT sum(x) FROM seq").fetchone()

    monkeypatch.setattr(layout, "_verify_connection", long_check)
    try:
        assert database.maintain(cancel) is False
        assert database.path.stat().st_ino == before
        assert database.connection().execute("SELECT 1").fetchone()[0] == 1
        assert not tuple(database.data_directory.glob("*.invalid-*"))
    finally:
        database.close()


def test_sqlite_busy_is_not_treated_as_repository_corruption(tmp_path: Path, monkeypatch) -> None:
    database = RepositoryDatabase(tmp_path / "data")
    database.initialize()
    before = database.path.stat().st_ino

    def busy(_connection):
        error = sqlite3.OperationalError("database is locked")
        error.sqlite_errorcode = sqlite3.SQLITE_BUSY
        raise error

    monkeypatch.setattr(layout, "_verify_connection", busy)
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            database.maintain(threading.Event())
        assert database.path.stat().st_ino == before
        assert database.connection().execute("SELECT 1").fetchone()[0] == 1
    finally:
        database.close()


def test_corrupt_hardlinked_cache_remains_unavailable_without_changing_other_path(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    path = data / "repository.sqlite"
    damaged = b"invalid cache header"
    path.write_bytes(damaged)
    path.chmod(0o600)
    outside = tmp_path / "outside"
    outside.hardlink_to(path)
    store = SessionStore(data)
    try:
        store.initialize()
        assert store.health_state == "ready"
        with pytest.raises(layout.StorageError, match="repository_cache_unavailable"):
            store.repository_database.connection()
        assert path.stat().st_ino == outside.stat().st_ino
        assert outside.read_bytes() == damaged
        assert not tuple(data.glob("*.invalid-*"))
    finally:
        store.close()


def test_future_repository_schema_is_preserved_and_only_indexing_is_unavailable(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "data")
    store.initialize()
    path = store.repository_database.path
    store.close()
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=999")
    before = path.stat().st_ino
    reopened = SessionStore(tmp_path / "data")
    try:
        reopened.initialize()
        assert reopened.health_state == "ready"
        with pytest.raises(layout.StorageError, match="repository_cache_unavailable"):
            reopened.repository_database.connection()
        assert reopened.maintain_repository_cache(threading.Event()) is False
        assert path.stat().st_ino == before
        with sqlite3.connect(path) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 999
        assert not tuple(path.parent.glob("*.invalid-*"))
    finally:
        reopened.close()


def test_missing_cache_tables_rebuild_without_blocking_runtime(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    path = data / "repository.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript("CREATE TABLE unrelated_cache(id); PRAGMA user_version=1;")
    path.chmod(0o600)
    store = SessionStore(data)
    try:
        store.initialize()
        assert store.health_state == "ready"
        assert store.repository_database.connection().execute("SELECT COUNT(*) FROM repository_snapshots").fetchone()[0] == 0
        assert len(tuple(data.glob("repository.sqlite.invalid-*"))) == 1
    finally:
        store.close()


def test_cache_symlink_is_disabled_without_touching_target(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.write_bytes(b"must remain")
    outside.chmod(0o600)
    path = data / "repository.sqlite"
    path.symlink_to(outside)
    store = SessionStore(data)
    try:
        store.initialize()
        assert store.health_state == "ready"
        with pytest.raises(layout.StorageError, match="repository_cache_unavailable"):
            store.repository_database.connection()
        assert store.maintain_repository_cache(threading.Event()) is False
        assert path.is_symlink()
        assert outside.read_bytes() == b"must remain"
        assert not tuple(data.glob("*.invalid-*"))
    finally:
        store.close()


def test_background_check_does_not_hold_repository_query_lock(tmp_path: Path, monkeypatch) -> None:
    database = RepositoryDatabase(tmp_path / "data")
    database.initialize()
    started, release = threading.Event(), threading.Event()
    verify = layout._verify_connection
    outcomes = []

    def blocked_check(connection):
        assert connection is not database.connection()
        started.set()
        assert release.wait(3)
        verify(connection)

    monkeypatch.setattr(layout, "_verify_connection", blocked_check)
    worker = threading.Thread(target=lambda: outcomes.append(database.maintain(threading.Event())))
    worker.start()
    try:
        assert started.wait(2)
        assert database.lock.acquire(timeout=0.2)
        try:
            with database.transaction() as connection:
                connection.execute("CREATE TABLE concurrent_cache_write(id)")
        finally:
            database.lock.release()
        release.set()
        worker.join(3)
        assert not worker.is_alive()
        assert outcomes == [False]
    finally:
        release.set()
        worker.join(3)
        database.close()
