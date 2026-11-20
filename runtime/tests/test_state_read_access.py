from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from pathlib import Path

import pytest

from eidos_runtime.db.connection_access import MonitoredRLock, ReadConnectionPool
from eidos_runtime.db.json_blobs import JsonBlobReference
from eidos_runtime.db import layout
from eidos_runtime.db.storage import SessionStore


def _store(tmp_path: Path) -> SessionStore:
    store = SessionStore(tmp_path / "data")
    store.initialize()
    assert store.health_state == "ready"
    return store


def _workspace(tmp_path: Path) -> str:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return str(workspace)


def test_monitored_lock_is_reentrant_and_reports_long_holds(caplog: pytest.LogCaptureFixture) -> None:
    lock = MonitoredRLock(name="state", report_seconds=0.0)
    with caplog.at_level(logging.WARNING, logger="eidos.runtime.storage"):
        with lock:
            assert lock._is_owned()
            with lock:
                assert lock._is_owned()
            assert lock._is_owned()
        assert not lock._is_owned()
    holds = [record.getMessage() for record in caplog.records if "storage_lock_held" in record.getMessage()]
    # Only the outermost release reports, with the holder thread name.
    assert len(holds) == 1
    assert "lock=state" in holds[0] and "thread=MainThread" in holds[0]


def test_monitored_lock_rejects_release_by_another_thread() -> None:
    lock = MonitoredRLock(name="state")
    lock.acquire()
    errors: list[BaseException] = []

    def release() -> None:
        try:
            lock.release()
        except RuntimeError as error:
            errors.append(error)

    worker = threading.Thread(target=release)
    worker.start()
    worker.join()
    lock.release()
    assert errors


def test_read_transaction_does_not_wait_for_the_writer_lock(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        session = store.create_session(_workspace(tmp_path))
        holding = threading.Event()
        release = threading.Event()

        def hold_writer() -> None:
            with store.database.lock:
                holding.set()
                release.wait(5)

        writer = threading.Thread(target=hold_writer)
        writer.start()
        assert holding.wait(5)
        try:
            with store.database.read_transaction() as connection:
                row = connection.execute("SELECT id FROM sessions WHERE id = ?", (session["id"],)).fetchone()
            assert row["id"] == session["id"]
        finally:
            release.set()
            writer.join()
    finally:
        store.close()


def test_read_transaction_is_one_snapshot(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        store.create_session(_workspace(tmp_path))
        with store.database.read_transaction() as connection:
            before = connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            store.create_session(_workspace(tmp_path))
            during = connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        with store.database.read_transaction() as connection:
            after = connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        assert (before, during, after) == (1, 1, 2)
    finally:
        store.close()


def test_read_connection_is_read_only(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        with pytest.raises(sqlite3.Error):
            with store.database.read_transaction() as connection:
                connection.execute("DELETE FROM sessions")
    finally:
        store.close()


def test_read_pool_refuses_a_replaced_database_file(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite"
    sqlite3.connect(path).close()
    pool = ReadConnectionPool(path)
    replacement = tmp_path / "replacement.sqlite"
    sqlite3.connect(replacement).close()
    os.replace(replacement, path)
    with pytest.raises(sqlite3.OperationalError, match="identity changed"):
        with pool.transaction():
            pass
    pool.close()


def test_blob_collection_reads_references_without_holding_the_state_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    try:
        blobs = store.database.json_blobs
        orphan = JsonBlobReference.from_json(blobs.put_json("orphan", '{"unused":true}'))
        assert orphan is not None
        observed: list[bool] = []
        original = layout.iter_blob_references

        def references(connection: sqlite3.Connection):
            # Another thread must be able to take the writer lock mid-collection.
            acquired = threading.Event()

            def writer() -> None:
                if store.database.lock.acquire(timeout=2):
                    acquired.set()
                    store.database.lock.release()

            thread = threading.Thread(target=writer)
            thread.start()
            thread.join()
            observed.append(acquired.is_set())
            yield from original(connection)

        monkeypatch.setattr(layout, "iter_blob_references", references)
        assert store.collect_unreferenced_blobs() is None
        assert observed == [True]
        assert not (blobs.root / orphan.relative_path).exists()
    finally:
        store.close()


def test_blob_written_during_collection_is_not_deleted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        blobs = store.database.json_blobs
        stale = blobs.put_json("orphan", '{"stale":true}')
        reused = blobs.put_json("orphan", '{"reused":true}')
        with blobs.collection() as collection:
            retained = collection.mark([], cancel=None)
            assert retained == set()
            # A concurrent writer creates one Blob and reuses an old one after
            # the reference snapshot; neither may be deleted by this sweep.
            fresh = blobs.put_json("orphan", '{"fresh":true}')
            assert blobs.put_json("orphan", '{"reused":true}') == reused
            assert collection.sweep(retained, cancel=None) == 1
        for stored in (fresh, reused):
            reference = JsonBlobReference.from_json(stored)
            assert reference is not None
            assert json.loads(blobs.read(reference))
        stale_reference = JsonBlobReference.from_json(stale)
        assert stale_reference is not None
        assert not (blobs.root / stale_reference.relative_path).exists()
    finally:
        store.close()


def test_pinned_context_manifest_keeps_its_children(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        blobs = store.database.json_blobs
        child = blobs.put_json("context-item", json.dumps({"role": "user", "content": "hi"}))
        tools = blobs.put_json("context-tools", "[]")
        with blobs.collection() as collection:
            retained = collection.mark([], cancel=None)
            # A manifest written mid-collection references children that were
            # created before the snapshot and are not otherwise pinned.
            with blobs.lock:
                pins = blobs._collection_pins
                assert len(pins) == 1
                pins[0].clear()
            manifest = blobs.put_json("context-snapshot-v2", json.dumps({
                "$eidosContextBlocks": 1,
                "snapshot": {},
                "modelContextRefs": [child],
                "toolDefinitionsRef": tools,
            }))
            collection.sweep(retained, cancel=None)
        for stored in (manifest, child, tools):
            reference = JsonBlobReference.from_json(stored)
            assert reference is not None
            assert (blobs.root / reference.relative_path).exists()
    finally:
        store.close()


def test_reads_and_collection_fall_back_when_no_read_connection_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    try:
        session = store.create_session(_workspace(tmp_path))
        orphan = JsonBlobReference.from_json(
            store.database.json_blobs.put_json("orphan", '{"unused":true}')
        )
        assert orphan is not None
        pool = store.database._read_pool
        assert pool is not None

        def unavailable() -> sqlite3.Connection:
            raise sqlite3.OperationalError("unable to open database file")

        monkeypatch.setattr(pool, "_take", unavailable)
        assert store.read_session_snapshot(session["id"])["session"]["id"] == session["id"]
        assert store.list_events(session["id"], after_event_id=0)["items"] is not None
        store.collect_unreferenced_blobs()
        assert not (store.database.json_blobs.root / orphan.relative_path).exists()
    finally:
        store.close()


def test_startup_lock_holds_are_not_reported_but_later_holds_are(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    store = SessionStore(tmp_path / "data")
    store.database.lock._report_seconds = 0.0
    with caplog.at_level(logging.WARNING, logger="eidos.runtime.storage"):
        store.initialize()
        assert not [r for r in caplog.records if "storage_lock" in r.getMessage()]
        with store.database.lock:
            pass
    try:
        assert [r for r in caplog.records if "storage_lock_held" in r.getMessage()]
    finally:
        store.close()
