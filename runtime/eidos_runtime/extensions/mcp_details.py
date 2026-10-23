"""Bounded, display-only MCP discovery cache; never changes a Run's tools."""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock
import json
import time
import uuid
from typing import cast

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.extensions import McpServerRecord
from eidos_runtime.db.database import canonical_hash, now_ms
from eidos_runtime.extensions.mcp import McpConnection, McpUnavailable, _tool_entry
from eidos_runtime.extensions.contracts import McpServerConfigV1
from eidos_runtime.extensions.plugins import MANUAL_MCP_PLUGIN_ID, PluginCatalog
from eidos_runtime.models.mcp_details import McpToolDetail, McpToolsRequest, McpToolsResponse
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
from eidos_runtime.runtime.resource_registry import ResourceRegistry


@dataclass(frozen=True)
class _Catalog:
    identity: str
    expires: float
    fetched_at: int
    description: str | None
    tools: tuple[McpToolDetail, ...]


class McpDetails:
    def __init__(self, plugins: Callable[[], PluginCatalog | None],
                 kernel: Callable[[], RuntimeAsyncKernel], resources: ResourceRegistry,
                 *, sandbox: bool = True) -> None:
        self._plugins = plugins
        self._kernel = kernel
        self._resources = resources
        self._sandbox = sandbox
        self._lock = Lock()
        self._catalogs: OrderedDict[str, _Catalog] = OrderedDict()

    def read(self, request: McpToolsRequest, cancel: Event) -> McpToolsResponse:
        plugins = self._plugins()
        if plugins is None:
            raise ApplicationError("RUNTIME_NOT_INITIALIZED")
        server = self._server(plugins, request)
        if not server.consented or not server.declared_enabled:
            return McpToolsResponse(state="not_authorized")
        if request.plugin_id != MANUAL_MCP_PLUGIN_ID and not any(
            item["id"] == request.plugin_id and item["enabled"] for item in plugins.list_plugins()
        ):
            return McpToolsResponse(state="not_authorized")
        identity = canonical_hash(server.to_wire_dict())
        if request.cursor:
            catalog_id, offset_text = request.cursor.split(":")
            with self._lock:
                catalog = self._catalogs.get(catalog_id)
            if catalog is None or catalog.expires < time.monotonic() or catalog.identity != identity:
                raise ApplicationError("INVALID_CURSOR")
            offset = int(offset_text)
        else:
            if cancel.is_set():
                raise ApplicationError("RUNTIME_DRAINING")
            try:
                catalog = self._discover(plugins, server, identity, cancel)
            except McpUnavailable as error:
                return McpToolsResponse(state="error", error_code=str(error))
            if canonical_hash(self._server(plugins, request).to_wire_dict()) != identity or cancel.is_set():
                raise ApplicationError("INVALID_CURSOR")
            catalog_id, offset = str(uuid.uuid4()), 0
            with self._lock:
                for key in tuple(self._catalogs):
                    if self._catalogs[key].expires < time.monotonic():
                        del self._catalogs[key]
                while len(self._catalogs) >= 8:
                    self._catalogs.popitem(last=False)
                self._catalogs[catalog_id] = catalog
        query = request.query.strip().casefold()
        tools = tuple(tool for tool in catalog.tools if not query or query in (
            f"{tool.name}\n{tool.description or ''}".casefold()
        ))
        if offset > len(tools):
            raise ApplicationError("INVALID_CURSOR")
        page = list(tools[offset:offset + 10])
        end = offset + len(page)
        return McpToolsResponse(
            state="ready", description=catalog.description, tools=page, total=len(tools),
            catalog_id=catalog_id, next_cursor=f"{catalog_id}:{end}" if end < len(tools) else None,
            fetched_at=catalog.fetched_at,
        )

    @staticmethod
    def _server(plugins: PluginCatalog, request: McpToolsRequest) -> McpServerRecord:
        server = next((item for item in plugins.list_mcp_servers() if
                       item["pluginId"] == request.plugin_id and item["serverId"] == request.server_id), None)
        if server is None:
            raise ApplicationError("RESOURCE_NOT_FOUND")
        return McpServerRecord.from_wire(server)

    def _discover(self, plugins: PluginCatalog, server: McpServerRecord,
                  identity: str, cancel: Event) -> _Catalog:
        plugin_id, server_id = server.plugin_id, server.server_id
        env: dict[str, str] = {}
        if plugin_id == MANUAL_MCP_PLUGIN_ID:
            manual = plugins.manual_mcp_server_config(server_id)
            config = cast(McpServerConfigV1, manual["config"])
            root = Path(str(manual["cwd"]))
            env = cast(dict[str, str], manual["env"])
        else:
            config = next(item for item in plugins.manifest(plugin_id).mcp_servers if item.id == server_id)
            root = plugins.installed_root(plugin_id)
        # Discovery must finish before the Desktop's ordinary RPC deadline.
        config = config.model_copy(update={"startup_timeout_seconds": min(config.startup_timeout_seconds, 15)})
        connection = McpConnection(
            plugin_root=root, workspace_root=root, config=config, env_values=env,
            on_list_changed=lambda: None, async_kernel=self._kernel(),
            sandbox=self._sandbox, resource_registry=self._resources,
            remote_client=(lambda: plugins.connections.http_client(plugin_id, config)) if config.transport == "streamable-http" else None,
        )
        try:
            raw_tools = connection.start()
            if cancel.is_set():
                raise ApplicationError("RUNTIME_DRAINING")
            tools = []
            for tool in raw_tools:
                available = True
                try:
                    _tool_entry(connection, tool, server.to_wire_dict())
                except (TypeError, ValueError):
                    available = False
                tools.append(McpToolDetail(
                    name=tool.name, description=(tool.description or "").strip()[:4096] or None,
                    input_schema=tool.input_schema, available=available,
                ))
            description = (connection.description or "").strip()[:4096] or None
            encoded = json.dumps({"description": description, "tools": [
                tool.to_wire_dict() for tool in tools
            ]}, ensure_ascii=False)
            if len(encoded.encode("utf-8")) > 512 * 1024:
                raise McpUnavailable("mcp_tool_list_too_large")
            return _Catalog(identity, time.monotonic() + 300, now_ms(), description, tuple(tools))
        finally:
            connection.close()
