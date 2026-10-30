from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
import json
import os
from pathlib import Path
import sqlite3
import uuid

import pytest

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.db.projectless_migration import migrate_projectless_workspaces
from eidos_runtime.db.schema import V22_SCHEMA_SQL
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.collaboration import SpawnAgent
from eidos_runtime.persistence.collaboration import CollaborationRepository
from eidos_runtime.persistence.projectless_reset import discard_legacy_projectless_sessions
from eidos_runtime.protocol.methods import (
    SessionCreateRequestDto, SessionCreateResponseDto, SessionRenameRequestDto,
)
from eidos_runtime.workspace.projectless import (
    MAX_NAME_BYTES,
    ProjectlessDirectoryAllocator,
    projectless_root,
    readable_workspace_name,
)
from eidos_runtime.sandbox.permissions import base_permission_profile_for_workspace, materialize_effective_profile
from eidos_runtime.sandbox.seatbelt import (
    SeatbeltProfile, is_seatbelt_usable, run_sandboxed, secure_workspace_move,
)


@pytest.fixture
def store(tmp_path: Path):
    storage = SessionStore(tmp_path / "data")
    storage.initialize()
    assert storage.health_state == "ready"
    yield storage
    storage.close()


def application(store: SessionStore) -> SessionApplication:
    return SessionApplication(store, scan_text=lambda value: value)


def request(name: str = "整理销售数据", operation: str | None = None) -> SessionCreateRequestDto:
    return SessionCreateRequestDto(
        workspaceNameHint=name, operationId=operation_id(operation) if operation else None,
    )


def operation_id(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))


@pytest.mark.parametrize(("hint", "name"), [
    ("  整理销售数据  ", "整理销售数据"),
    ("../修复/登录\\页面\x00", "修复-登录-页面"),
    ("... / \x00", "新会话"),
    (None, "新会话"),
    ("ＡＢＣ test", "ABC-test"),
])
def test_readable_names_are_safe_components(hint: str | None, name: str) -> None:
    assert readable_workspace_name(hint) == name
    assert len(readable_workspace_name("中文" * 512).encode()) <= MAX_NAME_BYTES


def test_default_root_uses_real_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EIDOS_PROJECTLESS_ROOT")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert projectless_root() == tmp_path / "Eidos"


def test_shared_projectless_protocol_fixture() -> None:
    fixture = json.loads((Path(__file__).resolve().parents[2]
        / "protocol/fixtures/projectless-v1.json").read_text())
    create = SessionCreateRequestDto.model_validate(fixture["request"]["params"])
    result = SessionCreateResponseDto.model_validate(fixture["response"]["result"])
    assert create.workspace_name_hint == "整理销售数据"
    assert result.root["projectless"] is True
    assert result.root["executionMode"] == "local"


def test_create_replay_and_rename_keep_one_directory(store: SessionStore, tmp_path: Path) -> None:
    app = application(store)
    created = app.create(request(operation="readable-create")).root
    root = Path(created["workspaceRoot"])
    assert root == tmp_path / "Eidos" / date.today().isoformat() / "整理销售数据"
    assert root.stat().st_mode & 0o777 == 0o700
    assert app.create(request(operation="readable-create")).root == created
    app.rename(SessionRenameRequestDto(sessionId=created["id"], title="新的标题"))
    assert app.create(request(operation="readable-create")).root["workspaceRoot"] == str(root)
    with pytest.raises(ApplicationError) as error:
        app.create(request("另一个任务", "readable-create"))
    assert error.value.code == "OPERATION_ID_REUSED"
    assert store.database.connection().execute(
        "SELECT count(*) FROM events WHERE event_type = 'session.created'"
    ).fetchone()[0] == 1


@pytest.mark.parametrize(("hint", "expected"), [
    ("中" * 32, "中" * 16),
    ("a" * 96, "a" * 48),
    ("a" * 47 + "中", "a" * 47),
])
def test_new_directory_names_use_48_bytes(
    store: SessionStore, hint: str, expected: str,
) -> None:
    app = application(store)
    created = app.create(request(hint, "short-name")).root
    root = Path(created["workspaceRoot"])
    assert root.name == expected
    assert root.is_dir()
    assert app.create(request(hint, "short-name")).root == created
    duplicate = app.create(request(hint)).root
    assert Path(duplicate["workspaceRoot"]).name == expected + "-2"


@pytest.mark.parametrize("completed", [False, True])
@pytest.mark.parametrize(("hint", "expected"), [("中" * 32, "中" * 16), ("a" * 96, "a" * 48)])
def test_existing_long_directory_names_survive_restart(
    store: SessionStore, monkeypatch: pytest.MonkeyPatch, completed: bool, hint: str, expected: str,
) -> None:
    create_request = request(hint, "existing-long-name")
    with monkeypatch.context() as legacy:
        legacy.setattr("eidos_runtime.persistence.projectless_workspaces.MAX_NEW_NAME_BYTES", 96)
        if completed:
            created = application(store).create(create_request).root
            root = Path(created["workspaceRoot"])
        else:
            repository = store.typed_runtime_repository().projectless_workspaces()
            creation = repository.prepare(create_request.operation_id, hint, projectless_root())
            identity = ProjectlessDirectoryAllocator().create(Path(creation.workspace_root))
            repository.mark_created(creation, identity)
            root = identity.path
    (root / "keep.txt").write_text("existing content")
    data = store.data_directory
    store.close()
    store.__init__(data)
    store.initialize()
    assert store.health_state == "ready"
    restored = application(store).create(create_request).root
    assert restored["workspaceRoot"] == str(root)
    assert root.name == hint
    assert (root / "keep.txt").read_text() == "existing content"
    new = application(store).create(request(hint)).root
    assert Path(new["workspaceRoot"]).name == expected


def test_concurrent_and_existing_names_allocate_numeric_suffixes(store: SessionStore, tmp_path: Path) -> None:
    parent = tmp_path / "Eidos" / date.today().isoformat()
    parent.mkdir(parents=True)
    occupied = parent / "整理销售数据"
    occupied.mkdir()
    (occupied / "user.txt").write_text("keep")
    app = application(store)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda index: app.create(request(operation=f"parallel-{index}")), range(2)))
    assert {Path(result.root["workspaceRoot"]).name for result in results} == {
        "整理销售数据-2", "整理销售数据-3",
    }
    assert (occupied / "user.txt").read_text() == "keep"


def test_same_operation_concurrently_returns_one_session(store: SessionStore) -> None:
    app = application(store)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: app.create(request(operation="same-operation")), range(2)))
    assert results[0].root == results[1].root
    assert len(store.list_sessions()["items"]) == 1


@pytest.mark.parametrize("project_first", [False, True])
def test_operation_id_cannot_switch_between_project_and_projectless(
    store: SessionStore, tmp_path: Path, project_first: bool,
) -> None:
    app = application(store)
    root = tmp_path / "project"
    root.mkdir()
    project_request = SessionCreateRequestDto(workspaceRoot=str(root), operationId=operation_id("same-kind"))
    requests = [project_request, request(operation="same-kind")]
    if not project_first:
        requests.reverse()
    app.create(requests[0])
    with pytest.raises(ApplicationError) as error:
        app.create(requests[1])
    assert error.value.code == "OPERATION_ID_REUSED"
    assert len(store.list_sessions()["items"]) == 1


def test_created_reservation_survives_restart_and_midnight(store: SessionStore, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = store.typed_runtime_repository().projectless_workspaces()
    monkeypatch.setattr("eidos_runtime.persistence.projectless_workspaces.creation_date", lambda _: "2026-06-22")
    creation = repository.prepare(operation_id("restart-create"), "整理销售数据", projectless_root())
    identity = ProjectlessDirectoryAllocator().create(Path(creation.workspace_root))
    repository.mark_created(creation, identity)
    data = store.data_directory
    store.close()
    store.__init__(data)
    store.initialize()
    assert store.health_state == "ready"
    monkeypatch.setattr("eidos_runtime.persistence.projectless_workspaces.creation_date", lambda _: "2026-06-23")
    created = application(store).create(request(operation="restart-create")).root
    assert created["id"] == creation.session_id
    assert created["workspaceRoot"] == creation.workspace_root
    assert Path(created["workspaceRoot"]).parent.name == "2026-06-22"


def test_unowned_prepared_directory_is_never_adopted(store: SessionStore) -> None:
    repository = store.typed_runtime_repository().projectless_workspaces()
    creation = repository.prepare(operation_id("prepared-create"), "整理销售数据", projectless_root())
    orphan = Path(creation.workspace_root)
    orphan.mkdir(parents=True)
    (orphan / "keep.txt").write_text("external content")
    created = application(store).create(request(operation="prepared-create")).root
    assert Path(created["workspaceRoot"]).name == "整理销售数据-2"
    assert (orphan / "keep.txt").read_text() == "external content"


def test_replaced_created_directory_fails_closed(store: SessionStore) -> None:
    repository = store.typed_runtime_repository().projectless_workspaces()
    creation = repository.prepare(operation_id("changed-create"), "整理销售数据", projectless_root())
    identity = ProjectlessDirectoryAllocator().create(Path(creation.workspace_root))
    repository.mark_created(creation, identity)
    identity.path.rename(identity.path.with_name("original"))
    identity.path.mkdir()
    with pytest.raises(ApplicationError) as error:
        application(store).create(request(operation="changed-create"))
    assert error.value.code == "SESSION_PERSISTENCE_FAILED"
    assert store.list_sessions()["items"] == []


def test_sql_failure_rolls_back_session_event_and_owned_empty_directory(
    store: SessionStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_event(*args, **kwargs):
        raise sqlite3.OperationalError("injected event failure")
    monkeypatch.setattr("eidos_runtime.db.repositories.sessions.append_event", fail_event)
    with pytest.raises(ApplicationError) as error:
        application(store).create(request(operation="failed-create"))
    assert error.value.code == "SESSION_PERSISTENCE_FAILED"
    connection = store.database.connection()
    assert connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
    assert connection.execute("SELECT count(*) FROM events").fetchone()[0] == 0
    assert connection.execute("SELECT count(*) FROM projectless_workspace_creations").fetchone()[0] == 0
    assert not (tmp_path / "Eidos" / date.today().isoformat() / "整理销售数据").exists()


def test_failed_session_commit_preserves_nonempty_workspace_and_can_retry(
    store: SessionStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import eidos_runtime.db.repositories.sessions as sessions
    original = sessions.append_event
    root = projectless_root() / date.today().isoformat() / "整理销售数据"
    def fail_with_file(*args, **kwargs):
        (root / "keep.txt").write_text("concurrent content")
        raise sqlite3.OperationalError("injected event failure")
    monkeypatch.setattr(sessions, "append_event", fail_with_file)
    with pytest.raises(ApplicationError):
        application(store).create(request(operation="nonempty-create"))
    assert (root / "keep.txt").read_text() == "concurrent content"
    assert store.list_sessions()["items"] == []
    monkeypatch.setattr(sessions, "append_event", original)
    assert application(store).create(request(operation="nonempty-create")).root["workspaceRoot"] == str(root)
    assert (root / "keep.txt").read_text() == "concurrent content"


def test_corrupted_reservation_cannot_create_outside_managed_root(store: SessionStore, tmp_path: Path) -> None:
    repository = store.typed_runtime_repository().projectless_workspaces()
    creation = repository.prepare(operation_id("bad-root"), "整理销售数据", projectless_root())
    outside = tmp_path / "outside" / "2026-06-22" / "整理销售数据"
    with store.database.transaction() as connection:
        connection.execute(
            "UPDATE projectless_workspace_creations SET workspace_root=? WHERE operation_id=?",
            (str(outside), creation.operation_id),
        )
    with pytest.raises(ApplicationError) as error:
        application(store).create(request(operation="bad-root"))
    assert error.value.code == "WORKSPACE_BOUNDARY_VIOLATION"
    assert not outside.parent.parent.exists()


@pytest.mark.parametrize("field", ["base_name", "workspace_root"])
def test_corrupted_reservation_cannot_traverse_through_date_or_name(store: SessionStore, field: str) -> None:
    repository = store.typed_runtime_repository().projectless_workspaces()
    creation = repository.prepare(operation_id("bad-component"), "整理销售数据", projectless_root())
    value = "../../escape" if field == "base_name" else str(projectless_root() / ".." / creation.base_name)
    with store.database.transaction() as connection:
        connection.execute(
            f"UPDATE projectless_workspace_creations SET {field}=? WHERE operation_id=?",
            (value, creation.operation_id),
        )
    with pytest.raises(ApplicationError) as error:
        application(store).create(request(operation="bad-component"))
    assert error.value.code == "SESSION_PERSISTENCE_FAILED"
    assert not (projectless_root().parent / "escape").exists()
    assert not (projectless_root().parent / creation.base_name).exists()


def test_configured_root_cannot_overlap_runtime_data(store: SessionStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EIDOS_PROJECTLESS_ROOT", str(store.data_directory / "workspaces"))
    with pytest.raises(ApplicationError) as error:
        application(store).create(request())
    assert error.value.code == "WORKSPACE_BOUNDARY_VIOLATION"
    assert not (store.data_directory / "workspaces").exists()


def test_symlink_date_directory_fails_without_writing_target(
    store: SessionStore, tmp_path: Path,
) -> None:
    root = tmp_path / "Eidos"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / date.today().isoformat()).symlink_to(outside, target_is_directory=True)
    with pytest.raises(ApplicationError):
        application(store).create(request())
    assert list(outside.iterdir()) == []


def test_deleted_session_operation_does_not_recreate_workspace(store: SessionStore) -> None:
    app = application(store)
    created = app.create(request(operation="deleted-create")).root
    store.delete_session(created["id"])
    with pytest.raises(ApplicationError) as error:
        app.create(request(operation="deleted-create"))
    assert error.value.code == "RESOURCE_NOT_FOUND"
    assert store.list_sessions()["items"] == []


def test_v22_schema_upgrade_is_atomic() -> None:
    connection = sqlite3.connect(":memory:")
    connection.executescript(V22_SCHEMA_SQL + "PRAGMA user_version = 22;")
    connection.set_authorizer(lambda action, arg1, *_: sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_CREATE_TABLE and arg1 == "projectless_workspace_creations"
        else sqlite3.SQLITE_OK)
    with pytest.raises(sqlite3.DatabaseError):
        migrate_projectless_workspaces(connection)
    connection.set_authorizer(None)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 22
    assert connection.execute("SELECT name FROM sqlite_master WHERE name = 'projectless_workspace_creations'").fetchone() is None
    migrate_projectless_workspaces(connection)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 23
    connection.close()


def test_alpha_reset_discards_old_facts_and_keeps_files_and_projects(
    store: SessionStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_loudly(database, error):
        raise error
    monkeypatch.setattr("eidos_runtime.db.database.Database.mark_failed", fail_loudly)
    project_root = tmp_path / "project"
    project_root.mkdir()
    project_session = store.create_session(str(project_root))
    old = application(store).create(request(operation="old-create")).root
    old_run, _ = store.create_run(old["id"], "old input")
    item = store.create_tool_item(old_run["id"], 1, 0, "spawn-old-child", "spawn_agent", "{}")
    child = CollaborationRepository(store.database).spawn(
        old_run["id"], item["id"], SpawnAgent(task_name="inspect", message="Inspect shared workspace"),
    )
    assert store.workspace_for_session(child.session_id) == store.workspace_for_session(old["id"])
    old_root = store.data_directory / f".{store.data_directory.name}-projectless" / "old-session"
    old_root.mkdir(parents=True)
    (old_root / "keep.txt").write_text("old artifact")
    metadata = old_root.stat()
    with store.database.transaction() as connection:
        connection.execute(
            "UPDATE sessions SET workspace_root=?,workspace_dev=?,workspace_inode=?,workspace_uid=? WHERE id IN (?, ?)",
            (str(old_root), metadata.st_dev, metadata.st_ino, metadata.st_uid, old["id"], child.session_id),
        )
        connection.execute("UPDATE runs SET reconciliation_required=1 WHERE id=?", (old_run["id"],))
    data = store.data_directory
    store.close()
    store.__init__(data)
    store.initialize()
    assert store.health_state == "ready"
    assert [item["id"] for item in store.list_sessions()["items"]] == [project_session["id"]]
    assert store.database.connection().execute("SELECT 1 FROM runs WHERE id=?", (old_run["id"],)).fetchone() is None
    assert store.database.connection().execute("SELECT 1 FROM sessions WHERE id=?", (child.session_id,)).fetchone() is None
    assert store.database.connection().execute("PRAGMA foreign_key_check").fetchall() == []
    assert (old_root / "keep.txt").read_text() == "old artifact"
    assert project_root.is_dir()
    assert os.environ["EIDOS_PROJECTLESS_ROOT"] == str(tmp_path / "Eidos")


def test_v22_state_store_upgrade_discards_old_session(tmp_path: Path) -> None:
    data = tmp_path / "old-data"
    data.mkdir(mode=0o700)
    old_root = data / f".{data.name}-projectless" / "old-session"
    old_root.mkdir(parents=True)
    metadata = old_root.stat()
    connection = sqlite3.connect(data / "state.sqlite")
    connection.executescript(V22_SCHEMA_SQL + "PRAGMA user_version = 22;")
    connection.execute(
        """INSERT INTO sessions (id, workspace_root, workspace_dev, workspace_inode,
           workspace_uid, execution_mode, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'local', 1, 1)""",
        (str(uuid.uuid4()), str(old_root), metadata.st_dev, metadata.st_ino, metadata.st_uid),
    )
    connection.commit()
    connection.close()
    (data / "state.sqlite").chmod(0o600)
    storage = SessionStore(data)
    storage.initialize()
    try:
        assert storage.health_state == "ready", storage.health_code
        assert storage.list_sessions()["items"] == []
        assert storage.database.connection().execute(
            "SELECT name FROM sqlite_master WHERE name = 'projectless_workspace_creations'"
        ).fetchone() is not None
        assert old_root.is_dir()
    finally:
        storage.close()


def test_alpha_reset_failure_rolls_back_status_and_deletion(
    store: SessionStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = application(store).create(request()).root
    run, _ = store.create_run(session["id"], "pending old task")
    legacy = store.data_directory / f".{store.data_directory.name}-projectless" / "obsolete"
    with store.database.transaction() as connection:
        connection.execute("UPDATE sessions SET workspace_root=? WHERE id=?", (str(legacy), session["id"]))
    def fail_invalidation(*args, **kwargs):
        raise sqlite3.OperationalError("injected reset failure")
    monkeypatch.setattr("eidos_runtime.memory.lifecycle.invalidate_source", fail_invalidation)
    with pytest.raises(sqlite3.OperationalError, match="injected reset failure"):
        discard_legacy_projectless_sessions(store.database)
    assert store.read_run(run["id"])["status"] == "running"
    assert store.read_session(session["id"])["id"] == session["id"]


@pytest.mark.platform
def test_new_projectless_native_seatbelt_write_scope(store: SessionStore) -> None:
    if not is_seatbelt_usable():
        pytest.skip("real macOS Seatbelt is unavailable")
    app = application(store)
    first = app.create(request(operation="native-first")).root
    second = app.create(request("另一个会话", "native-second")).root
    workspace = Path(first["workspaceRoot"])
    sibling = Path(second["workspaceRoot"])
    effective = materialize_effective_profile(
        base_permission_profile_for_workspace(workspace, store.data_directory)
    )
    candidate = workspace / ".candidate"
    target = workspace / "result.txt"
    candidate.write_text("native output")
    assert secure_workspace_move(workspace, candidate, target, None, effective_permissions=effective) == "committed"
    assert target.read_text() == "native output"
    home, temporary = workspace / "home", workspace / "tmp"
    home.mkdir()
    temporary.mkdir()
    profile = SeatbeltProfile.create(
        workspace_root=workspace, sandbox_home=home, sandbox_tmp=temporary,
        effective_permissions=effective,
    )
    own_write = run_sandboxed(profile, ["/bin/sh", "-c", 'printf own > "$1"', "sh", str(workspace / "shell.txt")])
    assert own_write.returncode == 0, own_write.stderr
    sibling_write = run_sandboxed(profile, ["/bin/sh", "-c", 'printf forbidden > "$1"', "sh", str(sibling / "forbidden.txt")])
    assert sibling_write.returncode != 0
    assert not (sibling / "forbidden.txt").exists()
    state_read = run_sandboxed(profile, ["/bin/sh", "-c", 'cat "$1" >/dev/null', "sh", str(store.data_directory / "state.sqlite")])
    assert state_read.returncode != 0
