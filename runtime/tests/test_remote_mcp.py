from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
from threading import Event, Thread
import time
import uuid

import anyio
import pytest
from mcp.shared.auth import OAuthToken

from eidos_runtime.application.builtin_plugins import builtin_plugins
from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.db.schema import V37_SCHEMA_SQL
from eidos_runtime.db.mcp_connections_migration import migrate_mcp_connections
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.db.database import now_ms
from eidos_runtime.extensions.mcp import McpManager
from eidos_runtime.extensions.plugin_package import read_package
from eidos_runtime.extensions.plugins import PluginCatalog
from eidos_runtime.extensions.skills import SkillCatalog
from eidos_runtime.infrastructure.mcp_auth import Credentials
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.models.mcp_connections import BuiltinPluginRequest, McpConnectionRequest
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.runtime.resource_registry import ResourceRegistry


@contextmanager
def remote_server():
    calls = []
    authorized = Event()
    authorized.set()
    entered = Event()
    release = Event()
    release.set()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            calls.append(payload.get("method"))
            if self.headers.get("Authorization") != "Bearer fixture-token" or not authorized.is_set():
                self.send_response(401)
                self.end_headers()
                return
            entered.set()
            release.wait(5)
            method = payload["method"]
            if "id" not in payload:
                self.send_response(202)
                self.end_headers()
                return
            if method == "initialize":
                result = {"protocolVersion": payload["params"]["protocolVersion"], "capabilities": {"tools": {}},
                          "serverInfo": {"name": "fixture", "version": "1.0"}}
            elif method == "tools/list":
                result = {"tools": [{"name": "read_issue", "description": "Read an issue", "inputSchema": {"type": "object", "properties": {}},
                                      "annotations": {"readOnlyHint": True}}]}
            else:
                result = {"content": [{"type": "text", "text": "issue details"}]}
            data = json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": result}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            self.send_response(405)
            self.end_headers()

        def do_DELETE(self):
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/mcp", calls, authorized, entered, release
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


def create_plugin(root: Path, url: str, auth_type: str = "bearer"):
    root.mkdir()
    (root / "plugin.json").write_text('{"name":"remote-demo","version":"1.0.0"}')
    (root / "mcp.json").write_text(json.dumps({"mcpServers": {"demo": {
        "type": "streamable-http", "url": url, "extensions": {"com.eidos": {"auth": {"type": auth_type}}},
    }}}))


@pytest.fixture
def catalog(tmp_path, request):
    with remote_server() as remote:
        create_plugin(tmp_path / "source", remote[0], getattr(request, "param", "bearer"))
        store = SessionStore(tmp_path / "data")
        store.initialize()
        plugins = PluginCatalog(store)
        plugins.import_directory(tmp_path / "source")
        plugins.set_enabled("remote-demo", True)
        resources = ResourceRegistry()
        kernel = RuntimeAsyncKernel(resource_registry=resources)
        kernel.start()
        try:
            yield plugins, kernel, remote
        finally:
            plugins.connections.close()
            kernel.close()
            assert resources.active_resources() == ()
            store.close()


def connect(plugins, kernel):
    return plugins.connections.execute(McpConnectionRequest(
        plugin_id="remote-demo", server_id="demo", action="connect", method="token", token="fixture-token",
        operation_id=str(uuid.uuid4()),
    ), kernel)


def settled(plugins):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        state = plugins.connections.read("remote-demo", "demo")
        if state.status != "connecting":
            return state
        time.sleep(0.02)
    raise AssertionError("connection did not settle")


def test_remote_credentials_restart_discovery_call_and_disconnect(catalog):
    plugins, kernel, remote = catalog
    assert connect(plugins, kernel).status in {"connecting", "connected"}
    assert settled(plugins).status == "connected"
    assert plugins.list_mcp_servers()[0]["available"]
    # A fresh catalog loads the credential reference after restart, without another login.
    restarted = PluginCatalog(plugins.store)
    manager = McpManager(restarted, restarted.extension_snapshot(), restarted.root,
                         async_kernel=kernel, sandbox=False)
    try:
        entries = manager.start()
        assert [entry.spec.name for entry in entries] == ["mcp__demo__read_issue"]
        result = manager.connections[0].call("read_issue", {}, Event())
        assert "issue details" in json.dumps(result)
        restarted.connections.execute(McpConnectionRequest(plugin_id="remote-demo", server_id="demo", action="disconnect"), kernel)
        before = remote[1].count("tools/call")
        result = manager.connections[0].call("read_issue", {}, Event())
        assert result["outcome"] == "error"
        assert remote[1].count("tools/call") == before
    finally:
        manager.close()
    assert "fixture-token" not in json.dumps(plugins.store.list_extension_events())
    row = plugins.connections.repository.get("remote-demo", "demo")
    assert row.credential_ref is None
    assert not list(plugins.connections.secrets.directory.iterdir())


def test_canceled_login_cannot_publish_a_late_token(catalog):
    plugins, kernel, remote = catalog
    remote[4].clear()
    connect(plugins, kernel)
    assert remote[3].wait(2)
    state = plugins.connections.execute(McpConnectionRequest(plugin_id="remote-demo", server_id="demo", action="cancel"), kernel)
    assert state.status == "disconnected"
    remote[4].set()
    time.sleep(0.1)
    assert plugins.connections.read("remote-demo", "demo").status == "disconnected"
    assert plugins.connections.repository.get("remote-demo", "demo").credential_ref is None


def test_revoked_token_marks_expired_and_does_not_retry_tools(catalog):
    plugins, kernel, remote = catalog
    connect(plugins, kernel)
    assert settled(plugins).status == "connected"
    remote[2].clear()
    manager = McpManager(plugins, plugins.extension_snapshot(), plugins.root, async_kernel=kernel, sandbox=False)
    assert manager.start() == ()
    assert plugins.connections.read("remote-demo", "demo").status == "expired"
    assert "tools/call" not in remote[1]


def test_missing_credential_file_expires_the_connection_before_tool_submission(catalog):
    plugins, kernel, remote = catalog
    connect(plugins, kernel)
    assert settled(plugins).status == "connected"
    record = plugins.connections.repository.get("remote-demo", "demo")
    (plugins.connections.secrets.directory / record.credential_ref).unlink()
    before = len(remote[1])
    manager = McpManager(plugins, plugins.extension_snapshot(), plugins.root, async_kernel=kernel, sandbox=False)
    try:
        assert manager.start() == ()
        assert plugins.connections.read("remote-demo", "demo").status == "expired"
        assert len(remote[1]) == before
    finally:
        manager.close()


def test_remote_cancellation_records_an_unknown_result_without_replaying_the_tool(catalog):
    plugins, kernel, remote = catalog
    connect(plugins, kernel)
    assert settled(plugins).status == "connected"
    manager = McpManager(plugins, plugins.extension_snapshot(), plugins.root, async_kernel=kernel, sandbox=False)
    manager.start()
    remote[3].clear()
    remote[4].clear()
    cancel = Event()
    results = []
    worker = Thread(target=lambda: results.append(manager.connections[0].call("read_issue", {}, cancel)))
    try:
        worker.start()
        assert remote[3].wait(2)
        cancel.set()
        worker.join(3)
        assert not worker.is_alive()
        assert results[0]["sideEffectsMayExist"] is True
        assert results[0]["code"] == "mcp_tool_canceled"
        assert remote[1].count("tools/call") == 1
    finally:
        remote[4].set()
        cancel.set()
        worker.join(3)
        manager.close()


def test_builtin_github_is_available_and_installation_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.delenv("EIDOS_GITHUB_CLIENT_ID", raising=False)
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        plugins = PluginCatalog(store)
        assert not builtin_plugins(plugins, BuiltinPluginRequest()).plugins[0].installed
        for _ in range(2):
            assert builtin_plugins(plugins, BuiltinPluginRequest(action="install")).plugins[0].installed
        assert len(plugins.list_plugins()) == 1
        assert plugins.manifest("eidos-github").mcp_servers[0].url == "https://api.githubcopilot.com/mcp/"
        assert not plugins.list_mcp_servers()[0]["available"]
        assert not plugins.connections.read("eidos-github", "github").browser_available
        with pytest.raises(ApplicationError, match="GITHUB_CLIENT_ID_REQUIRED"):
            plugins.connections.execute(McpConnectionRequest(plugin_id="eidos-github", server_id="github", action="connect", operation_id="attempt"), None)
    finally:
        store.close()


@pytest.mark.parametrize("url", ["https://user:secret@example.com/mcp", "http://example.com/mcp", "https://example.com/mcp?token=secret", "https://example.com/mcp#token"])
def test_remote_package_rejects_inline_credentials_and_insecure_urls(url):
    with pytest.raises(ValueError):
        read_package({"plugin.json": b'{"name":"remote","version":"1.0.0"}',
                      "mcp.json": json.dumps({"mcpServers": {"remote": {"type": "streamable-http", "url": url}}}).encode()})


def test_connection_migration_and_failed_migration_rollback():
    connection = sqlite3.connect(":memory:")
    connection.executescript(V37_SCHEMA_SQL + "PRAGMA user_version=37;")
    migrate_mcp_connections(connection)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 38
    connection.execute("DROP TABLE mcp_connections")
    connection.executescript("CREATE TABLE mcp_connections(occupied TEXT); PRAGMA user_version=37;")
    with pytest.raises(sqlite3.OperationalError):
        migrate_mcp_connections(connection)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 37
    assert connection.execute("PRAGMA table_info(mcp_connections)").fetchone()[1] == "occupied"
    connection.close()


def test_removed_plugin_erases_tokens_and_a_stale_credential_publish_is_rejected(catalog):
    from eidos_runtime.infrastructure.mcp_auth import McpAuthError
    plugins, kernel, _remote = catalog
    connect(plugins, kernel)
    assert settled(plugins).status == "connected"
    record = plugins.connections.repository.get("remote-demo", "demo")
    credentials = plugins.connections._load(record)
    plugins.remove("remote-demo")
    assert plugins.connections.repository.get("remote-demo", "demo").credential_ref is None
    assert not list(plugins.connections.secrets.directory.iterdir())
    with pytest.raises((McpAuthError, ApplicationError)):
        plugins.connections._publish(record, credentials)


def test_restart_interrupts_pending_login_without_replaying_network(catalog):
    plugins, kernel, remote = catalog
    remote[4].clear()
    connect(plugins, kernel)
    assert remote[3].wait(2)
    plugins.connections.close()
    before = len(remote[1])
    reloaded = PluginCatalog(plugins.store)
    state = reloaded.connections.read("remote-demo", "demo")
    assert state.status == "disconnected"
    assert state.error_code == "mcp_login_interrupted"
    assert len(remote[1]) == before
    remote[4].set()


def test_run_scoped_catalog_creation_does_not_interrupt_an_active_login(catalog):
    plugins, kernel, remote = catalog
    remote[4].clear()
    connect(plugins, kernel)
    assert remote[3].wait(2)
    view = PluginCatalog(plugins.store)
    assert view.connections.read("remote-demo", "demo").status == "connecting"
    remote[4].set()
    assert settled(plugins).status == "connected"
    assert view.connections.read("remote-demo", "demo").status == "connected"


@pytest.mark.parametrize("catalog", ["oauth"], indirect=True)
def test_concurrent_catalog_views_rotate_the_shared_credential_only_once(catalog, monkeypatch):
    plugins, kernel, remote = catalog
    connect(plugins, kernel)
    assert settled(plugins).status == "connected"
    record = plugins.connections.repository.get("remote-demo", "demo")
    plugins.connections._publish(record, Credentials(
        token=OAuthToken(access_token="fixture-token", refresh_token="fixture-refresh"),
        expires_at=now_ms() - 1000,
    ))
    second_view = PluginCatalog(plugins.store)
    rotations = []

    async def refresh(credentials):
        rotations.append(credentials.token.refresh_token)
        await anyio.sleep(0.05)
        return credentials.model_copy(update={
            "token": OAuthToken(access_token="fixture-token", refresh_token="rotated-refresh"),
            "expires_at": now_ms() + 3600000,
        })

    monkeypatch.setattr("eidos_runtime.application.mcp_connections.refresh_oauth", refresh)

    async def request_with(view):
        async with view.connections.http_client("remote-demo", view.manifest("remote-demo").mcp_servers[0]) as client:
            assert (await client.get(remote[0])).status_code == 405

    async def scenario():
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(request_with, plugins)
            tasks.start_soon(request_with, second_view)

    kernel.call(scenario)
    assert rotations == ["fixture-refresh"]
    assert second_view.connections._load(record).token.refresh_token == "rotated-refresh"


def test_remote_tool_uses_the_run_snapshot_and_durable_execution(catalog, tmp_path):
    plugins, kernel, remote = catalog
    connect(plugins, kernel)
    assert settled(plugins).status == "connected"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = plugins.store.create_session(str(workspace))
    snapshot = SkillCatalog(plugins).extension_snapshot()
    run, _ = plugins.store.create_run(session["id"], "Read the issue", extension_snapshot=snapshot)
    model = ScriptedModel([
        ModelResponse(tool_calls=(ModelToolCall("find", "tool_search", {
            "query": "mcp__demo__read_issue", "limit": 1,
        }),)),
        ModelResponse(tool_calls=(ModelToolCall("read", "mcp__demo__read_issue", {}),)),
        ModelResponse(text="Done."),
    ])

    def unexpected_approval(*_args):
        raise AssertionError("An explicit read-only remote tool must not request approval")

    RuntimeEngine(plugins.store, model, lambda _: None, request_approval=unexpected_approval,
                  async_kernel=kernel, mcp_sandbox=False).run(run["id"], Event())
    assert plugins.store.read_run(run["id"])["status"] == "succeeded"
    assert remote[1].count("tools/call") == 1
    intent = plugins.store.connection.execute(
        "SELECT preconditions_json,status FROM durable_intents WHERE run_id=?", (run["id"],),
    ).fetchone()
    assert intent["status"] == "completed"
    assert json.loads(intent["preconditions_json"])["authorization"] == "mcp_read_only"
    assert "fixture-token" not in json.dumps(snapshot)
    assert "fixture-token" not in json.dumps(plugins.store.list_extension_events())
    assert "fixture-token" not in repr(model.contexts)


def test_remote_package_rejects_a_non_boolean_disabled_flag():
    with pytest.raises(ValueError, match="plugin_mcp_invalid"):
        read_package({"plugin.json": b'{"name":"remote","version":"1.0.0"}', "mcp.json": json.dumps({
            "mcpServers": {"remote": {"type": "streamable-http", "url": "https://example.com/mcp", "disabled": "false"}},
        }).encode()})
