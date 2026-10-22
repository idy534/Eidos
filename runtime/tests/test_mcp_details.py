from __future__ import annotations

from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
import sys
from threading import Event
from unittest.mock import patch

from mcp import types as mcp_types
import pytest

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.extensions.mcp import McpUnavailable
from eidos_runtime.extensions.mcp_details import McpDetails
from eidos_runtime.extensions.plugins import PluginCatalog
from eidos_runtime.models.mcp_details import McpToolsRequest, McpToolsResponse
from eidos_runtime.protocol.server import RuntimeServer
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
from eidos_runtime.runtime.resource_registry import ResourceRegistry


@pytest.fixture
def catalog(tmp_path):
    root = tmp_path / "server"
    root.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    plugins = PluginCatalog(store)
    plugins.create_manual_mcp(
        server_id="demo", executable=sys.executable, argv=["server.py"], env={},
        env_names=[], cwd=str(root), permission_profile="workspace_read",
        startup_timeout_seconds=5, tool_timeout_seconds=10,
    )
    plugins.set_mcp_enabled("manual", "demo", True)
    resources = ResourceRegistry()
    kernel = RuntimeAsyncKernel(resource_registry=resources)
    kernel.start()
    service = McpDetails(lambda: plugins, lambda: kernel, resources, sandbox=False)
    try:
        yield service, plugins, root, kernel, resources
    finally:
        kernel.close()
        assert resources.active_resources() == ()
        store.close()


def request(**kwargs):
    return McpToolsRequest(plugin_id="manual", server_id="demo", **kwargs)


class FakeConnection:
    instances = []
    description = "A server description"

    def __init__(self, **kwargs):
        self.config = kwargs["config"]
        self.closed = False
        self.instances.append(self)

    def start(self):
        return tuple(mcp_types.Tool(name=f"tool_{index:02}", description=f"Description {index}",
            inputSchema={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]})
            for index in range(23))

    def close(self):
        self.closed = True


@pytest.fixture
def connection():
    FakeConnection.instances = []
    with patch("eidos_runtime.extensions.mcp_details.McpConnection", FakeConnection):
        yield FakeConnection


def test_pages_are_ten_and_searches_cover_unloaded_tools(catalog, connection):
    service, *_ = catalog
    first = service.read(request(), Event())
    assert first.description == "A server description"
    assert len(first.tools) == 10 and first.total == 23
    second = service.read(request(cursor=first.next_cursor), Event())
    third = service.read(request(cursor=second.next_cursor), Event())
    assert len(second.tools) == 10 and len(third.tools) == 3 and third.next_cursor is None
    assert len({tool.name for page in (first, second, third) for tool in page.tools}) == 23
    found = service.read(request(cursor=f"{first.catalog_id}:0", query="Description 22"), Event())
    assert [tool.name for tool in found.tools] == ["tool_22"]
    assert len(connection.instances) == 1 and connection.instances[0].closed


def test_unconsented_server_is_not_started_and_revocation_invalidates_pages(catalog, connection):
    service, plugins, *_ = catalog
    first = service.read(request(), Event())
    plugins.set_mcp_enabled("manual", "demo", False)
    result = service.read(request(cursor=first.next_cursor), Event())
    assert result.state == "not_authorized" and result.tools == []
    assert service.read(request(), Event()).state == "not_authorized"
    assert len(connection.instances) == 1


def test_expired_restart_and_configuration_cursors_are_rejected(catalog, connection):
    service, plugins, *_ = catalog
    first = service.read(request(), Event())
    with service._lock:
        service._catalogs[first.catalog_id] = replace(service._catalogs[first.catalog_id], expires=0)
    with pytest.raises(ApplicationError, match="INVALID_CURSOR"):
        service.read(request(cursor=first.next_cursor), Event())
    first = service.read(request(), Event())
    service._catalogs.clear()  # Runtime restart drops the display cache.
    with pytest.raises(ApplicationError, match="INVALID_CURSOR"):
        service.read(request(cursor=first.next_cursor), Event())
    first = service.read(request(), Event())
    plugins.update_manual_mcp(server_id="demo", executable=sys.executable, argv=["changed.py"],
        env={}, env_names=[], cwd=str(catalog[2]), permission_profile="workspace_read",
        startup_timeout_seconds=5, tool_timeout_seconds=10)
    plugins.set_mcp_enabled("manual", "demo", True)
    with pytest.raises(ApplicationError, match="INVALID_CURSOR"):
        service.read(request(cursor=first.next_cursor), Event())


def test_failure_and_cancellation_close_connection_without_tool_execution(catalog, connection):
    service, *_ = catalog
    with patch.object(connection, "start", side_effect=McpUnavailable("mcp_startup_timeout")):
        assert service.read(request(), Event()).error_code == "mcp_startup_timeout"
    assert connection.instances[-1].closed
    cancel = Event()
    original = connection.start
    def start(self):
        cancel.set()
        return original(self)
    with patch.object(connection, "start", start), pytest.raises(ApplicationError, match="RUNTIME_DRAINING"):
        service.read(request(), cancel)
    assert connection.instances[-1].closed


def test_cache_is_bounded_and_bad_schema_is_visible_as_unavailable(catalog, connection):
    service, *_ = catalog
    bad = mcp_types.Tool(name="invalid", inputSchema={"$ref": "https://example.com/schema"})
    with patch.object(connection, "start", return_value=(bad,)):
        assert service.read(request(), Event()).tools[0].available is False
    for _ in range(10):
        service.read(request(), Event())
    assert len(service._catalogs) == 8


@pytest.mark.parametrize("description", ["Actual server description", None])
def test_sdk_initialization_preserves_server_description_not_instructions(catalog, description):
    service, _, root, _, _ = catalog
    fixture = Path(__file__).parent / "fixtures" / "mcp_fixture.py"
    source = fixture.read_text().replace(
        '"serverInfo": {"name": "eidos-fixture", "version": "1.0.0"},',
        f'"serverInfo": {{"name": "eidos-fixture", "version": "1.0.0", "description": {description!r}}},'
        '\n                "instructions": "These are model instructions, not a description",',
    )
    (root / "server.py").write_text(source)
    result = service.read(request(), Event())
    assert result.state == "ready"
    assert result.description == description
    assert result.fetched_at and len(result.tools) == 10


def test_protocol_is_deferred_and_keeps_input_loop_responsive(tmp_path):
    output = StringIO()
    server = RuntimeServer(output, tmp_path / "data")
    try:
        server.handle({"jsonrpc": "2.0", "id": "client-init", "method": "initialize",
            "params": {"client": {"name": "test", "version": "1"}, "protocolVersion": 1}})
        entered, release = Event(), Event()
        def read(_request, _cancel):
            entered.set()
            assert release.wait(5)
            return McpToolsResponse(state="ready")
        with patch.object(server._applications_or_error().extensions, "read_mcp_tools", side_effect=read):
            server.handle({"jsonrpc": "2.0", "id": "client-tools", "method": "mcp/tools",
                "params": {"pluginId": "manual", "serverId": "demo"}})
            assert entered.wait(2)
            server.handle({"jsonrpc": "2.0", "id": "client-list", "method": "mcp/list", "params": {}})
            assert any(json.loads(line).get("id") == "client-list" for line in output.getvalue().splitlines())
            release.set()
            assert server.supervisor.wait_managed_tasks(3)
        reply = next(json.loads(line) for line in output.getvalue().splitlines() if json.loads(line).get("id") == "client-tools")
        assert reply["result"]["state"] == "ready"
    finally:
        server.close()


def test_shared_fixture_has_closed_contracts():
    from pydantic import ValidationError
    fixture = json.loads((Path(__file__).parents[2] / "protocol/fixtures/mcp-details-v1.json").read_text())
    McpToolsRequest.model_validate(fixture["request"])
    McpToolsResponse.model_validate(fixture["response"])
    with pytest.raises(ValidationError):
        McpToolsRequest.model_validate({**fixture["request"], "limit": 20})
    with pytest.raises(ValidationError):
        McpToolsResponse.model_validate({**fixture["response"], "tools": fixture["response"]["tools"] * 11})
