from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
import sqlite3
import stat
import zipfile

import pytest

from eidos_runtime.db.backup import FORMAT, backup, restore
from eidos_runtime.db.schema import SCHEMA_VERSION
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.memory.contracts import MemoryEditRequest, MemoryReadRequest
from eidos_runtime.memory.repository import MemoryRejected

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("schema_version", [40, SCHEMA_VERSION])
def test_data_backup_round_trip_preserves_chat_notes_and_reads_by_default(tmp_path, schema_version):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.create_session(str(workspace))
    run, _ = store.create_run(session["id"], "保留聊天")
    service = store.database.memory
    service.edit(
        MemoryEditRequest(
            operation_id="save",
            scope="global",
            path="user_language.md",
            summary="语言偏好",
            content="用户希望使用中文。",
        )
    )
    archive = tmp_path / "backup.zip"
    backup(service, archive)
    store.close()
    if schema_version == 40:
        with zipfile.ZipFile(archive) as source:
            entries = {name: source.read(name) for name in source.namelist()}
        state = tmp_path / "v40.sqlite"
        state.write_bytes(entries["state.sqlite"])
        with sqlite3.connect(state) as connection:
            connection.execute("PRAGMA user_version=40")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        entries["state.sqlite"] = state.read_bytes()
        manifest = json.loads(entries["manifest.json"])
        manifest["schemaVersion"] = 40
        entries["manifest.json"] = json.dumps(manifest).encode()
        write_archive(archive, list(entries.items()))
    destination = tmp_path / "restored"
    restore(archive, destination)
    restored = SessionStore(destination)
    restored.initialize()
    try:
        assert restored.health()["state"] == "ready"
        assert restored.connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert restored.read_run(run["id"])["sessionId"] == session["id"]
        assert (
            restored.database.memory.read(
                MemoryReadRequest(scope="global", path="user_language.md")
            ).content
            == "用户希望使用中文。"
        )
        assert (
            restored.connection.execute("SELECT mode FROM memory_scopes").fetchone()[0]
            == "read"
        )
        assert not restored.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='memory_jobs'"
        ).fetchone()
    finally:
        restored.close()


def write_archive(
    path: Path, entries: list[tuple[str | zipfile.ZipInfo, bytes]]
) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, value in entries:
            archive.writestr(name, value)


@pytest.mark.parametrize(
    "name",
    [
        "../outside",
        "/absolute",
        "./state.sqlite",
        "memory/../../outside",
        "blobs\\outside",
        "unknown/file",
        "",
    ],
)
def test_restore_rejects_unsafe_or_noncanonical_members(tmp_path, name):
    archive = tmp_path / "bad.zip"
    if not name:
        name = "memory/"
    write_archive(archive, [(name, b"bad")])
    with pytest.raises(MemoryRejected, match="member_invalid"):
        restore(archive, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()
    assert not list(tmp_path.glob(".eidos-restore-*"))


def test_restore_rejects_links_duplicate_members_and_old_format(tmp_path):
    archive = tmp_path / "bad.zip"
    link = zipfile.ZipInfo("blobs/link")
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    for entries, code in (
        ([(link, b"outside")], "member_invalid"),
        ([("manifest.json", b"{}"), ("manifest.json", b"{}")], "member_invalid"),
        (
            [("manifest.json", b'{"format":"eidos-memory-backup-v1"}')],
            "format_unsupported",
        ),
    ):
        with pytest.warns(UserWarning) if len(entries) == 2 else nullcontext():
            write_archive(archive, entries)
        with pytest.raises(MemoryRejected, match=code):
            restore(archive, tmp_path / "restored")


def test_restore_rejects_database_references_outside_the_memory_tree(tmp_path):
    database = tmp_path / "state.sqlite"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            "CREATE TABLE memory_revisions(file_ref TEXT); CREATE TABLE memory_indexes(file_ref TEXT);"
        )
        connection.execute("INSERT INTO memory_revisions VALUES('../../outside')")
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    archive = tmp_path / "bad.zip"
    manifest = {
        "format": FORMAT,
        "schemaVersion": SCHEMA_VERSION,
        "memoryRefs": ["../../outside"],
        "encrypted": False,
    }
    write_archive(
        archive,
        [
            ("manifest.json", json.dumps(manifest).encode()),
            ("state.sqlite", database.read_bytes()),
        ],
    )
    with pytest.raises(MemoryRejected, match="memory_invalid"):
        restore(archive, tmp_path / "restored")


def test_restore_does_not_replace_an_existing_directory(tmp_path):
    destination = tmp_path / "restored"
    destination.mkdir()
    (destination / "keep").write_text("existing data")
    with pytest.raises(MemoryRejected, match="requires_new_directory"):
        restore(tmp_path / "missing.zip", destination)
    assert (destination / "keep").read_text() == "existing data"
