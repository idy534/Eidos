from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
import sqlite3
import stat
import subprocess
import zipfile
from uuid import uuid4

import pytest

from eidos_runtime.db.backup import FORMAT, backup, restore
from eidos_runtime.db.schema import SCHEMA_VERSION
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.application.projects import ProjectApplication
from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.git import WorktreeManager
from eidos_runtime.memory.contracts import MemoryEditRequest, MemoryReadRequest
from eidos_runtime.memory.repository import MemoryRejected
from eidos_runtime.protocol.methods import SessionCreateRequestDto
from eidos_runtime.protocol.project_workspaces import ProjectCreateFoldersRequest, ProjectRootInput

pytestmark = pytest.mark.integration


def test_backup_preserves_archived_notes_and_shared_body_versions(tmp_path):
    store = SessionStore(tmp_path / "data")
    store.initialize()
    service = store.database.memory
    service.edit(
        MemoryEditRequest(
            operation_id="save", scope="global", path="archived.md", content="归档正文"
        )
    )
    service.edit(
        MemoryEditRequest(
            operation_id="archive",
            scope="global",
            path="archived.md",
            action="delete",
            expected_revision=1,
        ),
        automatic=True,
        archive=True,
    )
    archive = tmp_path / "archive.zip"
    backup(service, archive)
    store.close()
    restore(archive, tmp_path / "restored")
    reopened = SessionStore(tmp_path / "restored")
    reopened.initialize()
    try:
        for revision in (1, 2):
            page = reopened.database.memory.read(
                MemoryReadRequest(scope="global", path="archived.md", revision=revision)
            )
            assert page.content == "归档正文" and page.document.archived
    finally:
        reopened.close()


def test_v46_backup_restore_pins_existing_worktree_gitdir_identity(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repository)], check=True)
    subprocess.run(["git", "config", "user.name", "Eidos Tests"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.email", "eidos-tests@example.com"], cwd=repository, check=True)
    (repository / "tracked.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=repository, check=True)

    store = SessionStore(tmp_path / "data")
    store.initialize()
    manager = WorktreeManager(store.database, managed_root=tmp_path / "managed-worktrees")
    projects = ProjectApplication(manager.repository, create_project=manager.create_project)
    sessions = SessionApplication(
        store, scan_text=lambda value: value, worktree_manager=manager,
    )
    project = projects.create_folders(ProjectCreateFoldersRequest(
        operationId=str(uuid4()),
        roots=[ProjectRootInput(id="root-primary", path=str(repository))],
        primaryRootId="root-primary",
    ))
    session = sessions.create(SessionCreateRequestDto(
        projectId=project.id, executionMode="worktree",
    )).root
    worktree_id = session["worktree"]["worktreeId"]
    archive = tmp_path / "backup.zip"
    backup(store.database.memory, archive)
    store.close()

    with zipfile.ZipFile(archive) as source:
        entries = [(name, source.read(name)) for name in source.namelist()]
    state_path = tmp_path / "legacy.sqlite"
    state_path.write_bytes(dict(entries)["state.sqlite"])
    with sqlite3.connect(state_path) as connection:
        connection.execute("ALTER TABLE worktrees DROP COLUMN git_dir_uid")
        connection.execute("ALTER TABLE worktrees DROP COLUMN git_dir_inode")
        connection.execute("ALTER TABLE worktrees DROP COLUMN git_dir_dev")
        connection.execute("PRAGMA user_version=46")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    legacy_state = state_path.read_bytes()
    manifest = json.loads(dict(entries)["manifest.json"])
    manifest["schemaVersion"] = 46
    entries = [
        (name, legacy_state if name == "state.sqlite" else json.dumps(manifest).encode() if name == "manifest.json" else value)
        for name, value in entries
    ]
    write_archive(archive, entries)

    destination = tmp_path / "restored"
    restore(archive, destination)
    restored = SessionStore(destination)
    restored.initialize()
    try:
        restored_manager = WorktreeManager(
            restored.database, managed_root=tmp_path / "managed-worktrees",
        )
        worktree = restored_manager.read_worktree(worktree_id)
        assert worktree.git_dir_identity is not None
    finally:
        restored.close()


@pytest.mark.parametrize("schema_version", [40, 41, 42, 43, 44, 45, 46, SCHEMA_VERSION])
def test_data_backup_round_trip_preserves_chat_notes_and_reads_by_default(
    tmp_path, schema_version, downgrade_project_workspaces_schema
):
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
    if schema_version < SCHEMA_VERSION:
        with zipfile.ZipFile(archive) as source:
            entries = {name: source.read(name) for name in source.namelist()}
        state = tmp_path / "legacy.sqlite"
        state.write_bytes(entries["state.sqlite"])
        with sqlite3.connect(state) as connection:
            if schema_version < 45:
                downgrade_project_workspaces_schema(connection)
            elif schema_version == 45:
                connection.execute("ALTER TABLE worktree_lifecycle_operations DROP COLUMN workspace_roots_json")
                connection.execute("ALTER TABLE worktree_lifecycle_operations DROP COLUMN project_revision")
                connection.execute("ALTER TABLE worktrees DROP COLUMN git_dir_uid")
                connection.execute("ALTER TABLE worktrees DROP COLUMN git_dir_inode")
                connection.execute("ALTER TABLE worktrees DROP COLUMN git_dir_dev")
            elif schema_version == 46:
                connection.execute("ALTER TABLE worktrees DROP COLUMN git_dir_uid")
                connection.execute("ALTER TABLE worktrees DROP COLUMN git_dir_inode")
                connection.execute("ALTER TABLE worktrees DROP COLUMN git_dir_dev")
            if schema_version < 44:
                connection.execute("DROP TABLE personalization_settings")
            if schema_version < 43:
                connection.execute("ALTER TABLE memory_documents DROP COLUMN archived")
            if schema_version < 42:
                connection.execute(
                    "ALTER TABLE memory_sources ADD COLUMN temporary INTEGER NOT NULL DEFAULT 0"
                )
            connection.execute(f"PRAGMA user_version={schema_version}")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        entries["state.sqlite"] = state.read_bytes()
        manifest = json.loads(entries["manifest.json"])
        manifest["schemaVersion"] = schema_version
        entries["manifest.json"] = json.dumps(manifest).encode()
        write_archive(archive, list(entries.items()))
    destination = tmp_path / "restored"
    restore(archive, destination)
    restored = SessionStore(destination)
    restored.initialize()
    try:
        assert restored.health()["state"] == "ready"
        assert (
            restored.connection.execute("PRAGMA user_version").fetchone()[0]
            == SCHEMA_VERSION
        )
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
