from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from eidos_runtime.protocol.server import RuntimeServer


def _response(output: StringIO, request_id: str) -> dict[str, object]:
    return next(
        json.loads(line)
        for line in output.getvalue().splitlines()
        if json.loads(line).get("id") == request_id
    )


def test_business_method_registry_has_no_legacy_server_handler_adapter(tmp_path) -> None:
    """Business RPCs must enter application use cases, not captured server calls."""

    server = RuntimeServer(StringIO(), data_directory=tmp_path / "data")

    handlers = {
        registration.name: registration.handler.__class__.__name__
        for registration in server.method_registry
    }

    assert set(handlers.values()) <= {
        "_ApplicationMethodAdapter",
        "_DeferredGitFetchAdapter",
        "_DeferredGitPullAdapter",
        "_DeferredGitPushAdapter",
        "_DeferredPluginImportAdapter",
        "_DeferredGitObserveAdapter",
        "_DeferredSessionForkAdapter",
        "_CustomProviderAdapter",
        "_McpToolsAdapter",
        "_BrowserTaskAdapter",
    }
    assert all(registration.error_mapper is not None for registration in server.method_registry)
    assert not {
        "create_session",
        "list_sessions",
        "read_session",
        "rename_session",
        "delete_session",
        "start_run",
        "cancel_run",
        "list_plugins",
        "import_plugin",
    } & set(RuntimeServer.__dict__)


def test_runtime_application_container_composes_all_phase_ef_boundaries(tmp_path) -> None:
    server = RuntimeServer(StringIO(), data_directory=tmp_path / "data")
    server.store.initialize()

    applications = server._build_applications()

    assert applications.sessions is not None
    assert applications.projects is not None
    assert applications.runs is not None
    assert applications.approvals is not None
    assert applications.models is not None
    assert applications.extensions is not None
    assert applications.repository_factory is not None
    assert applications.repository_runtime is server.repository_runtime
    assert applications.context is not None
    assert applications.task_lifecycle is not None
    assert applications.workspace is not None
    assert applications.review is not None

    registered = {registration.name for registration in server.method_registry}
    assert {
        "project/list", "project/delete",
        "session/gitStatus",
        "session/gitDiff", "session/gitStage",
        "session/gitCommit",
        "session/gitSwitchBranch", "session/gitCreateBranch",
        "session/gitRemoteStatus",
        "session/gitFetch", "session/gitPull", "session/gitPush",
        "session/gitMerge", "session/gitMergeAbort",
        "session/gitRebase", "session/gitRebaseContinue", "session/gitRebaseAbort",
        "run/resume", "run/cancel",
        "context/usage",
        "workspace/listDirectory", "workspace/readAsset", "workspace/readFilePreview",
        "review/listComments", "review/createComment", "review/deleteComment",
    } <= registered
    # Methods with no Desktop caller stay unregistered.
    assert not registered & {
        "run/status", "run/pause", "skill/read",
        "checkpoint/create", "checkpoint/list", "checkpoint/rewind", "checkpoint/fork",
    }
    server.close()


def test_agent_protocol_methods_validate_and_return_collaboration_state(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = StringIO()
    server = RuntimeServer(output, data_directory=tmp_path / "data")
    server.store.initialize()
    server.initialized = True
    session = server.store.create_session(str(workspace))

    try:
        server.handle({
            "jsonrpc": "2.0",
            "id": "client-agent-read",
            "method": "agent/read",
            "params": {"sessionId": session["id"]},
        })
        assert _response(output, "client-agent-read")["result"] == {
            "agents": [],
            "messages": [],
        }

        server.handle({
            "jsonrpc": "2.0",
            "id": "client-agent-invalid",
            "method": "agent/read",
            "params": {"sessionId": ""},
        })
        assert _response(output, "client-agent-invalid")["error"]["code"] == -32602
    finally:
        server.close()


def test_runtime_server_close_stops_repository_watchers(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    server = RuntimeServer(StringIO(), data_directory=tmp_path / "data")
    server.store.initialize()
    active = server.repository_runtime.activate_workspace(workspace)

    server.close()

    assert active.closed is True
    assert active.watcher_thread is not None
    assert active.watcher_thread.is_alive() is False


def test_browser_status_replies_off_the_request_loop(tmp_path: Path) -> None:
    """A starting Chrome holds the host lock; status must not stall other RPCs."""

    import threading
    from unittest.mock import patch

    from eidos_runtime.application.browser import BrowserApplication
    from eidos_runtime.models.browser import BrowserStatus

    output = StringIO()
    server = RuntimeServer(output, data_directory=tmp_path / "data")
    try:
        server.handle({"jsonrpc": "2.0", "id": "client-init", "method": "initialize",
            "params": {"client": {"name": "test", "version": "1"}, "protocolVersion": 1}})
        entered, release = threading.Event(), threading.Event()

        def status(_application, _request):
            entered.set()
            assert release.wait(5)
            return BrowserStatus(
                chrome_installed=False, running=False, plugin_installed=False,
                plugin_enabled=False, resource="unavailable",
            )

        with patch.object(BrowserApplication, "status", status):
            server.handle({"jsonrpc": "2.0", "id": "client-browser", "method": "browser/status", "params": {}})
            assert entered.wait(2)
            server.handle({"jsonrpc": "2.0", "id": "client-health", "method": "runtime/health", "params": {}})
            assert _response(output, "client-health")["result"]
            release.set()
            assert server.supervisor.wait_managed_tasks(3)
        assert _response(output, "client-browser")["result"]["resource"] == "unavailable"
    finally:
        server.close()
