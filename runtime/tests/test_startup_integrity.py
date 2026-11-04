from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from eidos_runtime.db import database as database_module
from eidos_runtime.db.database import INTEGRITY_STAMP_NAME, Database


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "data"
    path.mkdir(mode=0o700)
    return path


@pytest.fixture
def pragma_log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    original = database_module._verify_integrity

    def spy(connection: sqlite3.Connection) -> None:
        seen.append("full")
        original(connection)

    monkeypatch.setattr(database_module, "_verify_integrity", spy)
    return seen


def _open(data_dir: Path) -> Database:
    database = Database(data_dir)
    database.initialize()
    assert database.health_state == "ready"
    database.close()
    return database


def test_new_database_runs_full_check_and_writes_stamp(data_dir, pragma_log) -> None:
    _open(data_dir)
    assert pragma_log == ["full"]
    assert (data_dir / INTEGRITY_STAMP_NAME).exists()


def test_fresh_stamp_skips_full_check(data_dir, pragma_log) -> None:
    _open(data_dir)
    pragma_log.clear()
    _open(data_dir)
    assert pragma_log == []


@pytest.mark.parametrize("content", [None, "garbage", "0"])
def test_missing_invalid_or_stale_stamp_forces_full_check(data_dir, pragma_log, content) -> None:
    _open(data_dir)
    stamp = data_dir / INTEGRITY_STAMP_NAME
    if content is None:
        stamp.unlink()
    else:
        stamp.write_text(content)
    pragma_log.clear()
    _open(data_dir)
    assert pragma_log == ["full"]


def test_expired_stamp_forces_full_check(data_dir, pragma_log) -> None:
    _open(data_dir)
    stamp = data_dir / INTEGRITY_STAMP_NAME
    stamp.write_text(str(int(time.time()) - database_module.FULL_INTEGRITY_INTERVAL_SECONDS - 5))
    pragma_log.clear()
    _open(data_dir)
    assert pragma_log == ["full"]


def test_stamp_is_not_trusted_when_quick_check_fails(data_dir, pragma_log, monkeypatch) -> None:
    _open(data_dir)
    pragma_log.clear()
    original = database_module.Database._verify_startup_integrity

    class Wrapper:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self._c = connection

        def execute(self, sql: str, *a):
            if sql == "PRAGMA quick_check":
                return self._c.execute("SELECT 'bad'")
            return self._c.execute(sql, *a)

    monkeypatch.setattr(
        Database,
        "_verify_startup_integrity",
        staticmethod(lambda c, s, *, force_full: original(Wrapper(c), s, force_full=force_full)),
    )
    _open(data_dir)
    assert pragma_log == ["full"]


def test_corrupt_database_is_rejected_even_with_fresh_stamp(data_dir) -> None:
    _open(data_dir)
    path = data_dir / "state.sqlite"
    size = path.stat().st_size
    with open(path, "r+b") as handle:
        handle.seek(size // 2)
        handle.write(b"\xff" * 4096)
    database = Database(data_dir)
    database.initialize()
    assert database.health_state != "ready"
    database.close()
