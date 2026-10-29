from __future__ import annotations

import io
from pathlib import Path
import time
from uuid import uuid4

from eidos_runtime.application.projects import ProjectApplication
from eidos_runtime.domain.channels import ChannelConfig, ChannelMessage
from eidos_runtime.model.client import ModelResponse, ScriptedModel
from eidos_runtime.model.config import DEFAULT_MODEL_ID
from eidos_runtime.protocol.automations import AutomationDesktopRequest
from eidos_runtime.protocol.channels import ChannelSaveRequest
from eidos_runtime.protocol.server import RuntimeServer
from test_multi_workspace_execution import _create_project


def _server_with_shared_projects(tmp_path: Path):
    server = RuntimeServer(io.StringIO(), tmp_path / "data", model=ScriptedModel([ModelResponse(text="done")]))
    server.handle({"jsonrpc": "2.0", "id": "client-init", "method": "initialize", "params": {"protocolVersion": 1, "client": {"name": "test", "version": "1"}}})
    assert server.initialized
    primary, first_only, second_only = (tmp_path / name for name in ("primary", "a-only", "b-only"))
    for root in (primary, first_only, second_only):
        root.mkdir()
    projects = ProjectApplication(server.worktree_manager.repository)
    _create_project(projects, [("a-main", primary), ("a-only", first_only)], "a-main")
    second = _create_project(projects, [("b-main", primary), ("b-only", second_only)], "b-main")
    return server, second, second_only


def _assert_run(server: RuntimeServer, run_id: str, project_id: str, secondary: Path) -> None:
    deadline = time.monotonic() + 10
    while server.store.read_run(run_id)["status"] in {"queued", "running", "finalizing"} and time.monotonic() < deadline:
        time.sleep(.02)
    assert server.store.read_run(run_id)["status"] == "succeeded"
    session = server.store.typed_runtime_repository().read_session(server.store.read_run(run_id)["sessionId"])
    assert session.project_id == project_id
    assert [root.id for root in session.roots] == ["b-main", "b-only"]
    assert server.store.workspace_for_run(run_id).additional_roots[0].path == secondary


def test_automation_new_session_keeps_explicit_project_when_primary_is_shared(tmp_path: Path) -> None:
    server, project, secondary = _server_with_shared_projects(tmp_path)
    try:
        app = server._automation_application()
        task = app.execute(AutomationDesktopRequest.model_validate({
            "mode": "create", "kind": "cron", "name": "multi project", "prompt": "inspect",
            "schedule": {"expression": "*/10 * * * *"}, "model": {"modelId": DEFAULT_MODEL_ID},
            "projectId": project.id,
        }), context_session_id=None, operation_id=str(uuid4())).automation
        occurrence_id = app.run_now(task.id, str(uuid4())).occurrence_id
        deadline = time.monotonic() + 10
        occurrence = app.repository.occurrence(occurrence_id)
        while occurrence.status == "preparing" and time.monotonic() < deadline:
            time.sleep(.02)
            occurrence = app.repository.occurrence(occurrence_id)
        assert occurrence.status == "submitted", occurrence
        _assert_run(server, occurrence.run_id, project.id, secondary)
    finally:
        server.close()


def test_channel_new_session_keeps_explicit_project_when_primary_is_shared(tmp_path: Path) -> None:
    server, project, secondary = _server_with_shared_projects(tmp_path)
    try:
        service = server.channel_service
        service.active = lambda: False
        model = server.model_config.create(provider_id="deepseek", model_id=DEFAULT_MODEL_ID, api_key="test-key")
        app = service.application
        app.save(ChannelSaveRequest(config=ChannelConfig(client_id="app", model_id=model.id, project_id=project.id),
            expected_revision=0, client_secret="test-channel-secret"))
        connection = app.repository.connection("dingtalk")
        connection = connection.model_copy(update={"revision": 2, "config": connection.config.model_copy(update={"enabled": True})})
        app.repository.save(connection, 1)
        app.receive(connection, ChannelMessage(msg_id="multi", corp_id="corp", robot_code="robot", conversation_id="chat",
            conversation_type="2", sender_id="user", mentioned=True, text="inspect"))
        app.process_pending()
        row = server.store.connection.execute("SELECT id FROM channel_inbound").fetchone()
        inbound = app.repository.inbound(row[0])
        assert inbound.run_id is not None
        _assert_run(server, inbound.run_id, project.id, secondary)
    finally:
        server.close()
