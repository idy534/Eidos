from __future__ import annotations

from pathlib import Path
import json
from uuid import uuid4

import pytest

from test_multi_workspace_execution import _repository
from test_project_workspaces import _request, _server


pytestmark = pytest.mark.integration


def _project(server, output, roots: list[tuple[str, Path]], primary: str):
    response = _request(
        server,
        output,
        str(uuid4()),
        "project/createFolders",
        {
            "operationId": str(uuid4()),
            "name": "Draft review",
            "roots": [
                {"id": identifier, "path": str(path)} for identifier, path in roots
            ],
            "primaryRootId": primary,
        },
    )
    assert "result" in response, response
    return response["result"]


def test_draft_git_preview_reads_each_project_repository_without_creating_a_session(
    tmp_path: Path,
) -> None:
    primary = _repository(tmp_path / "primary", "primary\n")
    secondary = _repository(tmp_path / "secondary", "secondary\n")
    (primary / "repository-marker.txt").write_text("primary change\n")
    (secondary / "repository-marker.txt").write_text("secondary change\n")
    server, output = _server(tmp_path)
    try:
        project = _project(
            server, output, [("primary", primary), ("secondary", secondary)], "primary"
        )
        draft = f"draft-{uuid4()}"
        counts_before = tuple(
            server.store.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[
                0
            ]
            for table in ("projects", "sessions", "worktrees")
        )
        for root_id, text in (
            ("primary", "primary change"),
            ("secondary", "secondary change"),
        ):
            params = {"sessionId": draft, "projectId": project["id"], "rootId": root_id}
            status = _request(server, output, str(uuid4()), "session/gitStatus", params)
            assert "result" in status, status
            assert status["result"]["unstagedFiles"] == ["repository-marker.txt"]
            diff = _request(
                server,
                output,
                str(uuid4()),
                "session/gitDiff",
                {
                    **params,
                    "scope": "head",
                    "path": "repository-marker.txt",
                },
            )
            assert "result" in diff, diff
            assert text in diff["result"]["unifiedDiff"]
        assert (
            tuple(
                server.store.connection.execute(
                    f"SELECT count(*) FROM {table}"
                ).fetchone()[0]
                for table in ("projects", "sessions", "worktrees")
            )
            == counts_before
        )
        assert counts_before == (1, 0, 0)
    finally:
        server.close()


def test_draft_git_preview_rejects_another_projects_root_and_non_git_root(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "primary"
    primary.mkdir()
    secondary = _repository(tmp_path / "secondary", "secondary\n")
    other = _repository(tmp_path / "other", "other\n")
    server, output = _server(tmp_path)
    try:
        project = _project(
            server, output, [("plain", primary), ("secondary", secondary)], "plain"
        )
        _project(server, output, [("other", other)], "other")
        for root_id in ("plain", "other", "missing"):
            response = _request(
                server,
                output,
                str(uuid4()),
                "session/gitStatus",
                {
                    "sessionId": f"draft-{uuid4()}",
                    "projectId": project["id"],
                    "rootId": root_id,
                },
            )
            assert (
                response["error"]["data"]["code"] == "WORKSPACE_BOUNDARY_VIOLATION"
            ), response
        allowed = _request(
            server,
            output,
            str(uuid4()),
            "session/gitStatus",
            {
                "sessionId": f"draft-{uuid4()}",
                "projectId": project["id"],
                "rootId": "secondary",
            },
        )
        assert "result" in allowed, allowed
        missing = _request(
            server,
            output,
            str(uuid4()),
            "session/gitStatus",
            {
                "sessionId": f"draft-{uuid4()}",
                "projectId": "unknown",
                "rootId": "secondary",
            },
        )
        assert missing["error"]["data"]["code"] == "RESOURCE_NOT_FOUND", missing
    finally:
        server.close()


@pytest.mark.parametrize("method", ["session/gitStatus", "session/gitDiff"])
@pytest.mark.parametrize(
    "session_id,project_id",
    [("draft-not-a-uuid", "project"), (f"draft-{uuid4()}", None)],
)
def test_draft_git_preview_requires_a_valid_draft_and_saved_project(
    tmp_path: Path, method: str, session_id: str, project_id: str | None
) -> None:
    server, output = _server(tmp_path)
    try:
        params = {"sessionId": session_id, "rootId": "root"}
        if project_id:
            params["projectId"] = project_id
        response = _request(server, output, str(uuid4()), method, params)
        assert response["error"]["code"] == -32602, response
    finally:
        server.close()


def test_draft_git_preview_keeps_mutations_and_comments_bound_to_persisted_sessions(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path / "repository", "original\n")
    server, output = _server(tmp_path)
    try:
        _project(server, output, [("main", repository)], "main")
        params = {"sessionId": f"draft-{uuid4()}", "rootId": "main"}
        for method, extra in (
            (
                "session/gitStage",
                {"operationId": str(uuid4()), "paths": ["repository-marker.txt"]},
            ),
            (
                "session/gitCommit",
                {"operationId": str(uuid4()), "message": "must not commit"},
            ),
            ("review/listComments", {}),
        ):
            response = _request(
                server, output, str(uuid4()), method, {**params, **extra}
            )
            assert response["error"]["code"] == -32602, response
        assert (
            server.store.connection.execute("SELECT count(*) FROM sessions").fetchone()[
                0
            ]
            == 0
        )
    finally:
        server.close()


def test_draft_git_read_fixtures_match_the_runtime_contract() -> None:
    from eidos_runtime.protocol.methods import (
        SessionGitDiffRequestDto,
        SessionGitStatusRequestDto,
    )

    fixture = json.loads(
        (Path(__file__).resolve().parents[2] / "protocol/fixtures/v1.json").read_text()
    )["draftGitReview"]
    for name, model in (
        ("statusRequest", SessionGitStatusRequestDto),
        ("diffRequest", SessionGitDiffRequestDto),
    ):
        params = fixture[name]["params"]
        request = model.model_validate(params)
        assert request.session_id == params["sessionId"]
        assert request.project_id == params["projectId"]
        assert request.root_id == params["rootId"]
