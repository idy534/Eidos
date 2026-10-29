from __future__ import annotations

import io
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

import pytest

from eidos_runtime.db.database import Database
from eidos_runtime.db.layout import STATE_DATABASE_NAME
from eidos_runtime.db.schema import V44_SCHEMA_SQL
from eidos_runtime.protocol.server import RuntimeServer


def _request(
    server: RuntimeServer,
    output: io.StringIO,
    request_id: str,
    method: str,
    params: dict[str, object],
) -> dict[str, object]:
    client_request_id = f"client-{request_id}"
    server.handle(
        {
            "jsonrpc": "2.0",
            "id": client_request_id,
            "method": method,
            "params": params,
        }
    )
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        for line in output.getvalue().splitlines():
            message = json.loads(line)
            if message.get("id") == client_request_id:
                return message
        time.sleep(0.005)
    raise AssertionError(
        f"Runtime did not reply to {method} request {client_request_id}; "
        f"received: {output.getvalue().splitlines()}"
    )


def _server(tmp_path: Path) -> tuple[RuntimeServer, io.StringIO]:
    output = io.StringIO()
    server = RuntimeServer(output, data_directory=tmp_path / "data")
    server.store.initialize()
    server.initialized = True
    return server, output


def _create_project(
    server: RuntimeServer,
    output: io.StringIO,
    workspace: Path,
) -> dict[str, object]:
    response = _request(
        server,
        output,
        f"create-{uuid4()}",
        "project/create",
        {
            "name": "Initial name",
            "workspaceRoot": str(workspace),
            "operationId": str(uuid4()),
        },
    )
    return response["result"]


def _project_read(
    server: RuntimeServer,
    output: io.StringIO,
    project_id: str,
    request_id: str,
) -> dict[str, object]:
    response = _request(
        server,
        output,
        request_id,
        "project/read",
        {"projectId": project_id},
    )
    return response["result"]


def test_project_read_returns_the_persisted_primary_root_and_revision(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "main"
    workspace.mkdir()
    server, output = _server(tmp_path)

    try:
        created = _create_project(server, output, workspace)
        read = _project_read(server, output, created["id"], "read-project")

        assert read["id"] == created["id"]
        assert read["revision"] == 1
        assert read["workspaceRoot"] == str(workspace.resolve())
        assert len(read["roots"]) == 1
        assert read["roots"][0]["path"] == str(workspace.resolve())
        assert read["primaryRootId"] == read["roots"][0]["id"]
    finally:
        server.close()


def test_session_create_rejects_project_root_with_replaced_symlink_parent(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "primary"
    secondary_parent = tmp_path / "secondary-parent"
    secondary = secondary_parent / "workspace"
    outside = tmp_path / "outside"
    primary.mkdir()
    secondary.mkdir(parents=True)
    (outside / "workspace").mkdir(parents=True)
    server, output = _server(tmp_path)

    try:
        project = _create_project(server, output, primary)
        root = project["roots"][0]
        updated = _request(
            server,
            output,
            "add-secondary-root",
            "project/update",
            {
                "projectId": project["id"],
                "operationId": str(uuid4()),
                "expectedRevision": project["revision"],
                "roots": [
                    {"id": root["id"], "path": str(primary)},
                    {"id": "secondary-root", "path": str(secondary)},
                ],
                "primaryRootId": root["id"],
            },
        )["result"]
        displaced = secondary_parent.with_name("secondary-parent-original")
        secondary_parent.rename(displaced)
        secondary_parent.symlink_to(outside, target_is_directory=True)

        rejected = _request(
            server,
            output,
            "create-session-with-replaced-root",
            "session/create",
            {"projectId": updated["id"], "workspaceRoot": str(primary)},
        )

        draft_rejected = _request(
            server,
            output,
            "list-draft-workspace-with-replaced-root",
            "workspace/listDirectory",
            {
                "sessionId": "draft-00000000-0000-0000-0000-000000000001",
                "projectId": updated["id"],
                "workspaceRoot": str(secondary),
                "path": ".",
            },
        )

        assert rejected["error"]["data"]["code"] == "WORKSPACE_IDENTITY_CHANGED"
        assert draft_rejected["error"]["data"]["code"] == "WORKSPACE_UNAVAILABLE"
        assert server.store.connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    finally:
        server.close()


def test_project_update_preserves_project_identity_and_replays_idempotently(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    primary.mkdir()
    secondary.mkdir()
    server, output = _server(tmp_path)

    try:
        created = _create_project(server, output, primary)
        original_root = created["roots"][0]
        operation_id = str(uuid4())
        update = {
            "projectId": created["id"],
            "operationId": operation_id,
            "expectedRevision": 1,
            "name": "Renamed project",
            "roots": [
                {"id": original_root["id"], "path": str(primary)},
                {"id": "secondary-root", "path": str(secondary)},
            ],
            "primaryRootId": "secondary-root",
        }

        first = _request(server, output, "update-project", "project/update", update)
        replay = _request(
            server, output, "replay-project-update", "project/update", update
        )
        project = _project_read(server, output, created["id"], "read-updated-project")

        assert first["result"] == replay["result"]
        assert project["id"] == created["id"]
        assert project["name"] == "Renamed project"
        assert project["revision"] == 2
        assert project["primaryRootId"] == "secondary-root"
        assert project["workspaceRoot"] == str(secondary.resolve())
        assert {root["path"] for root in project["roots"]} == {
            str(primary.resolve()),
            str(secondary.resolve()),
        }

        reused_id = dict(update)
        reused_id["name"] = "Different request"
        reused = _request(
            server,
            output,
            "reuse-project-operation-id",
            "project/update",
            reused_id,
        )
        assert reused["error"]["data"]["code"] == "OPERATION_ID_REUSED"
    finally:
        server.close()


def test_project_update_rejects_a_stale_revision_without_mutating_configuration(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "main"
    workspace.mkdir()
    server, output = _server(tmp_path)

    try:
        created = _create_project(server, output, workspace)
        root = created["roots"][0]
        current = {
            "projectId": created["id"],
            "operationId": str(uuid4()),
            "expectedRevision": 1,
            "name": "Current name",
            "roots": [{"id": root["id"], "path": str(workspace)}],
            "primaryRootId": root["id"],
        }
        updated = _request(
            server, output, "update-project-current", "project/update", current
        )
        assert updated["result"]["revision"] == 2

        stale = dict(current)
        stale["operationId"] = str(uuid4())
        stale["name"] = "Stale name"
        rejected = _request(
            server, output, "update-project-stale", "project/update", stale
        )
        project = _project_read(server, output, created["id"], "read-after-stale")

        assert rejected["error"]["data"]["code"] == "PROJECT_CONFIGURATION_CONFLICT"
        assert project["revision"] == 2
        assert project["name"] == "Current name"
    finally:
        server.close()


def test_project_update_keeps_existing_session_roots_frozen(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    primary.mkdir()
    secondary.mkdir()
    server, output = _server(tmp_path)

    try:
        created = _create_project(server, output, primary)
        original_root = created["roots"][0]
        session_response = _request(
            server,
            output,
            "create-session-before-project-update",
            "session/create",
            {"workspaceRoot": str(primary)},
        )
        session_id = session_response["result"]["id"]
        update = {
            "projectId": created["id"],
            "operationId": str(uuid4()),
            "expectedRevision": 1,
            "name": "Project with another root",
            "roots": [
                {"id": original_root["id"], "path": str(primary)},
                {"id": "secondary-root", "path": str(secondary)},
            ],
            "primaryRootId": "secondary-root",
        }

        updated = _request(
            server, output, "update-project-after-session", "project/update", update
        )
        session = _request(
            server,
            output,
            "read-session-after-project-update",
            "session/read",
            {"sessionId": session_id},
        )["result"]["session"]

        assert updated["result"]["workspaceRoot"] == str(secondary.resolve())
        assert session["project"]["id"] == created["id"]
        assert session["workspaceRoot"] == str(primary.resolve())
        assert [root["path"] for root in session["roots"]] == [
            str(primary.resolve())
        ]
    finally:
        server.close()


def test_local_session_create_replay_survives_project_primary_root_change(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    primary.mkdir()
    secondary.mkdir()
    server, output = _server(tmp_path)

    try:
        created = _create_project(server, output, primary)
        operation_id = str(uuid4())
        request = {
            "projectId": created["id"],
            "workspaceRoot": str(primary),
            "operationId": operation_id,
        }
        first = _request(
            server, output, "create-session-once", "session/create", request
        )["result"]
        root = created["roots"][0]
        _request(
            server,
            output,
            "change-project-primary-root",
            "project/update",
            {
                "projectId": created["id"],
                "operationId": str(uuid4()),
                "expectedRevision": 1,
                "roots": [
                    {"id": root["id"], "path": str(primary)},
                    {"id": "secondary-root", "path": str(secondary)},
                ],
                "primaryRootId": "secondary-root",
            },
        )

        replay = _request(
            server,
            output,
            "create-session-replay",
            "session/create",
            {**request, "workspaceRoot": str(secondary)},
        )["result"]

        assert replay["id"] == first["id"]
        assert replay["workspaceRoot"] == str(primary.resolve())
        assert [entry["path"] for entry in replay["roots"]] == [
            str(primary.resolve())
        ]
    finally:
        server.close()


def test_legacy_local_session_create_replay_survives_project_root_change(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    other = tmp_path / "other"
    primary.mkdir()
    secondary.mkdir()
    other.mkdir()
    server, output = _server(tmp_path)

    try:
        project = _create_project(server, output, primary)
        operation_id = str(uuid4())
        legacy_request = {
            "workspaceRoot": str(primary.resolve()),
            "projectless": False,
        }
        created = server.store.typed_runtime_repository().create_session(
            str(primary),
            project_id=str(project["id"]),
            operation_id=operation_id,
            operation_request=legacy_request,
        ).value

        _request(
            server,
            output,
            "change-project-primary-root-after-legacy-create",
            "project/update",
            {
                "projectId": project["id"],
                "operationId": str(uuid4()),
                "expectedRevision": 1,
                "roots": [
                    {"id": project["roots"][0]["id"], "path": str(primary)},
                    {"id": "secondary-root", "path": str(secondary)},
                ],
                "primaryRootId": "secondary-root",
            },
        )

        replay = _request(
            server,
            output,
            "replay-legacy-local-session-create",
            "session/create",
            {
                "projectId": project["id"],
                "workspaceRoot": str(secondary),
                "operationId": operation_id,
            },
        )["result"]

        assert replay["id"] == created.id
        assert replay["workspaceRoot"] == str(primary.resolve())
        assert [root["path"] for root in replay["roots"]] == [
            str(primary.resolve())
        ]

        other_project = _create_project(server, output, other)
        reused = _request(
            server,
            output,
            "reuse-legacy-session-create-for-another-project",
            "session/create",
            {"projectId": other_project["id"], "operationId": operation_id},
        )
        assert reused["error"]["data"]["code"] == "OPERATION_ID_REUSED"
    finally:
        server.close()


def test_v44_to_v45_migration_preserves_project_and_session_bindings(
    tmp_path: Path,
) -> None:
    data_directory = tmp_path / "legacy-data"
    data_directory.mkdir(mode=0o700)
    workspace = tmp_path / "legacy-workspace"
    workspace.mkdir()
    metadata = workspace.stat()
    project_id = "legacy-project"
    session_id = str(uuid4())
    database_path = data_directory / STATE_DATABASE_NAME

    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(V44_SCHEMA_SQL)
        connection.execute("PRAGMA user_version=44")
        connection.execute(
            """
            INSERT INTO projects (
                id, name, workspace_root, git_repository_root, git_common_dir,
                created_at, updated_at
            ) VALUES (?, ?, ?, NULL, NULL, ?, ?)
            """,
            (project_id, "Legacy project", str(workspace.resolve()), 1000, 2000),
        )
        connection.execute(
            """
            INSERT INTO sessions (
                id, workspace_root, workspace_dev, workspace_inode, workspace_uid,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                str(workspace.resolve()),
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_uid,
                1000,
                2000,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    database_path.chmod(0o600)
    database = Database(data_directory)
    database.initialize()
    try:
        connection = database.connection()
        revision = connection.execute("PRAGMA user_version").fetchone()[0]
        project = connection.execute(
            "SELECT id,name,workspace_root,revision,primary_root_id FROM projects WHERE id=?",
            (project_id,),
        ).fetchone()
        root = connection.execute(
            "SELECT id,path,position FROM project_roots WHERE project_id=?",
            (project_id,),
        ).fetchone()
        session = connection.execute(
            "SELECT id,project_id,workspace_roots_json FROM sessions WHERE id=?",
            (session_id,),
        ).fetchone()

        assert revision == 46
        assert project["id"] == project_id
        assert project["name"] == "Legacy project"
        assert project["workspace_root"] == str(workspace.resolve())
        assert project["revision"] == 1
        assert project["primary_root_id"] == f"root_{project_id}"
        assert root["id"] == f"root_{project_id}"
        assert root["path"] == str(workspace.resolve())
        assert root["position"] == 0
        assert session["id"] == session_id
        assert session["project_id"] == project_id
        session_roots = json.loads(session["workspace_roots_json"])
        assert session_roots == [
            {
                "id": f"root_{project_id}",
                "path": str(workspace.resolve()),
                "device": metadata.st_dev,
                "inode": metadata.st_ino,
                "owner": metadata.st_uid,
                "repository_root": None,
                "git_dir": None,
                "git_common_dir": None,
            }
        ]
    finally:
        database.close()


def test_project_update_replay_survives_a_missing_directory_and_runtime_restart(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    primary.mkdir()
    server, output = _server(tmp_path)
    created = _create_project(server, output, primary)
    root = created["roots"][0]
    request = {
        "projectId": created["id"], "operationId": str(uuid4()), "expectedRevision": 1,
        "name": "saved", "roots": [{"id": root["id"], "path": str(primary)}], "primaryRootId": root["id"],
    }
    first = _request(server, output, "first", "project/update", request)
    server.close()
    primary.rmdir()
    server, output = _server(tmp_path)
    try:
        assert _request(server, output, "replay", "project/update", request) == {"jsonrpc": "2.0", "id": "client-replay", "result": first["result"]}
    finally:
        server.close()


def test_workspace_migration_failure_rolls_back_schema_and_restores_foreign_keys() -> None:
    from eidos_runtime.db.project_workspaces_migration import migrate_project_workspaces
    connection = sqlite3.connect(":memory:")
    connection.executescript(V44_SCHEMA_SQL)
    connection.execute("PRAGMA user_version=44")
    connection.execute("PRAGMA foreign_keys=ON")
    # Force a failure after the Project table rebuild has already started.
    connection.execute("CREATE TABLE project_roots (fixture_marker TEXT)")
    connection.commit()
    try:
        with pytest.raises(sqlite3.OperationalError, match="already exists"):
            migrate_project_workspaces(connection)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 44
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert "revision" not in {row[1] for row in connection.execute("PRAGMA table_info(projects)")}
        assert "workspace_roots_json" not in {row[1] for row in connection.execute("PRAGMA table_info(sessions)")}
    finally:
        connection.close()
