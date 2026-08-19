from __future__ import annotations

import io
import json
from pathlib import Path
from uuid import uuid4

from eidos_runtime.protocol.server import RuntimeServer


def _request(
    server: RuntimeServer,
    output: io.StringIO,
    request_id: str,
    method: str,
    params: dict[str, object],
) -> dict[str, object]:
    server.handle(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
    )
    return next(
        json.loads(line)
        for line in output.getvalue().splitlines()
        if json.loads(line).get("id") == request_id
    )


def _server(tmp_path: Path) -> tuple[RuntimeServer, io.StringIO]:
    output = io.StringIO()
    server = RuntimeServer(output, data_directory=tmp_path / "data")
    server.store.initialize()
    server.initialized = True
    return server, output


def test_project_survives_last_session_delete_and_can_be_deleted_explicitly(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    marker = workspace / "keep.txt"
    marker.write_text("keep\n", encoding="utf-8")
    server, output = _server(tmp_path)

    try:
        created = _request(
            server,
            output,
            "client-create",
            "session/create",
            {"workspaceRoot": str(workspace)},
        )
        project = created["result"]["project"]
        session_id = created["result"]["id"]
        project_id = project["id"]

        deleted_session = _request(
            server,
            output,
            "client-delete-session",
            "session/delete",
            {"sessionId": session_id},
        )
        assert deleted_session["result"] == {"deletedSessionId": session_id}

        listed = _request(server, output, "client-list-projects", "project/list", {})
        assert listed["result"]["items"][0]["id"] == project_id

        server.close()
        server, output = _server(tmp_path)
        listed_after_restart = _request(
            server, output, "client-list-projects-after-restart", "project/list", {}
        )
        assert listed_after_restart["result"]["items"][0]["id"] == project_id

        operation_id = str(uuid4())
        deleted_project = _request(
            server,
            output,
            "client-delete-project",
            "project/delete",
            {"projectId": project_id, "operationId": operation_id},
        )
        assert deleted_project["result"] == {"deletedProjectId": project_id}
        assert marker.read_text(encoding="utf-8") == "keep\n"

        replayed_delete = _request(
            server,
            output,
            "client-delete-project-replay",
            "project/delete",
            {"projectId": project_id, "operationId": operation_id},
        )
        assert replayed_delete["result"] == {"deletedProjectId": project_id}

        listed_after_delete = _request(
            server, output, "client-list-projects-after-delete", "project/list", {}
        )
        assert listed_after_delete["result"] == {"items": []}
    finally:
        server.close()


def test_project_delete_prunes_legacy_empty_sessions(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    server, output = _server(tmp_path)

    try:
        created = _request(
            server,
            output,
            "client-create",
            "session/create",
            {"workspaceRoot": str(workspace)},
        )
        project_id = created["result"]["project"]["id"]

        deleted = _request(
            server,
            output,
            "client-delete-project",
            "project/delete",
            {"projectId": project_id, "operationId": str(uuid4())},
        )

        assert deleted["result"] == {"deletedProjectId": project_id}
        sessions = _request(server, output, "client-list-sessions", "session/list", {})
        assert sessions["result"]["items"] == []
    finally:
        server.close()


def test_project_delete_rejects_project_with_a_run(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    server, output = _server(tmp_path)

    try:
        created = _request(
            server,
            output,
            "client-create",
            "session/create",
            {"workspaceRoot": str(workspace)},
        )
        project_id = created["result"]["project"]["id"]
        session_id = created["result"]["id"]
        server.store.create_run(session_id, "a real task")

        rejected = _request(
            server,
            output,
            "client-delete-project-with-run",
            "project/delete",
            {"projectId": project_id, "operationId": str(uuid4())},
        )

        assert rejected["error"]["data"]["code"] == "PROJECT_HAS_SESSIONS"
    finally:
        server.close()


def test_project_create_persists_named_project_without_creating_session(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    server, output = _server(tmp_path)

    try:
        created = _request(
            server,
            output,
            "client-create-project",
            "project/create",
            {
                "name": "研究项目",
                "workspaceRoot": str(workspace),
                "operationId": str(uuid4()),
            },
        )

        project = created["result"]
        assert project["name"] == "研究项目"
        assert project["workspaceRoot"] == str(workspace.resolve())
        assert project["gitAvailable"] is False

        sessions = _request(server, output, "client-list-sessions", "session/list", {})
        assert sessions["result"]["items"] == []

        projects = _request(server, output, "client-list-projects", "project/list", {})
        assert projects["result"]["items"] == [project]
    finally:
        server.close()
