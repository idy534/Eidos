from __future__ import annotations

import io
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

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


def test_v44_to_v45_migration_preserves_project_and_session_bindings(
    tmp_path: Path,
) -> None:
    data_directory = tmp_path / "legacy-data"
    data_directory.mkdir()
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

        assert revision == 45
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
