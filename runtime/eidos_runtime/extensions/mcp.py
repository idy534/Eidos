from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import signal
import shutil
import sys
import threading
import time
import uuid
from typing import Callable

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp import types as mcp_types

from eidos_runtime.extensions.contracts import McpServerConfigV1
from eidos_runtime.extensions.plugins import PluginCatalog
from eidos_runtime.tools.registry import ToolProvenance, ToolRegistryEntry, ToolSpec
from eidos_runtime.tools.contracts import McpResultData, result_model
from eidos_runtime.tools.json_schema import (
    BoundedJsonSchema,
    JsonSchemaValidationError,
    validate_bounded_json_value,
)
from eidos_runtime.runtime.resource_registry import (
    ResourceRegistry,
    RuntimeResource,
    RuntimeResourceKind,
)
from eidos_runtime.runtime.async_kernel import (
    AsyncKernelClosedError,
    RuntimeAsyncKernel,
    RuntimeAsyncTask,
)
from eidos_runtime.runtime.fault_injection import hit_fault


MAX_LIST_PAGES = 32
MAX_TOOLS = 512
MAX_SCHEMA_BYTES = 256 * 1024
MAX_RESULT_BYTES = 256 * 1024
SANDBOX_EXECUTABLE = "/usr/bin/sandbox-exec"
SANDBOX_DIR = Path(__file__).resolve().parents[1] / "sandbox"
LAUNCHER = Path(__file__).with_name("mcp_launcher.py")

# The SDK includes the polluted stdout line in parser exception logs. Eidos maps
# that condition to a closed code and never logs the untrusted protocol bytes.
logging.getLogger("mcp.client.stdio").setLevel(logging.CRITICAL)
LOGGER = logging.getLogger(__name__)


class McpUnavailable(RuntimeError):
    pass


class McpShutdownTimeout(RuntimeError):
    pass


@dataclass
class _Command:
    name: str
    arguments: dict[str, object]
    timeout_seconds: int
    cancel: threading.Event


class McpConnection:
    def __init__(
        self,
        *,
        plugin_root: Path,
        runtime_root: Path,
        workspace_root: Path,
        config: McpServerConfigV1,
        on_list_changed: Callable[[], None],
        async_kernel: RuntimeAsyncKernel,
        sandbox: bool = True,
        resource_registry: ResourceRegistry | None = None,
    ) -> None:
        self.plugin_root = plugin_root
        self.workspace_root = workspace_root
        self.config = config
        self.on_list_changed = on_list_changed
        self.async_kernel = async_kernel
        self.sandbox = sandbox
        self.closed = threading.Event()
        self.tools: tuple[mcp_types.Tool, ...] = ()
        self.error_code: str | None = None
        self.runtime_root = runtime_root / uuid.uuid4().hex
        self.resources = resource_registry
        self.resource: RuntimeResource | None = None
        self._service: RuntimeAsyncTask[None] | None = None
        self._session: ClientSession | None = None
        self._session_lock: anyio.Lock | None = None
        self._close_event: anyio.Event | None = None
        self._callback_lock = threading.Lock()
        self._callbacks_in_flight = 0
        self._callbacks_quiescent = threading.Event()
        self._callbacks_quiescent.set()

    def start(self) -> tuple[mcp_types.Tool, ...]:
        if self.resources is not None:
            self.resource = self.resources.register(
                RuntimeResourceKind.MCP_CONNECTION,
                owner_id=self.config.id,
                cancel=self.closed.set,
            )
            self.resource.start()
        try:
            service, started = self.async_kernel.start_service(
                self._serve,
                owner_id=f"mcp:{self.config.id}",
                # Readiness has its own private timeout in _serve. A ready MCP
                # connection is a long-lived service, not a deadline-bound task.
                deadline=None,
            )
            self._service = service
            self.tools = tuple(started)
            return self.tools
        except BaseException as error:
            self.error_code = (
                str(error)
                if isinstance(error, McpUnavailable)
                else "mcp_connection_lost"
            )
            self.close()
            raise McpUnavailable(self.error_code) from None

    def call(
        self,
        name: str,
        arguments: dict[str, object],
        cancel: threading.Event,
    ) -> dict[str, object]:
        service = self._service
        if (
            self.error_code is not None
            or service is None
            or service.done()
            or self.closed.is_set()
        ):
            return _unavailable()
        command = _Command(name, arguments, self.config.tool_timeout_seconds, cancel)
        resource = (
            self.resources.register(
                RuntimeResourceKind.MCP_COMMAND,
                owner_id=self.config.id,
                cancel=cancel.set,
            )
            if self.resources is not None
            else None
        )
        if resource is not None:
            resource.start()
        try:
            try:
                result = self.async_kernel.call(self._call, command)
            except (AsyncKernelClosedError, RuntimeError):
                return _uncertain(self.error_code or "mcp_connection_lost")
            if self.error_code == "mcp_stdout_pollution":
                return _uncertain("mcp_stdout_pollution")
            if result.get("code") in {"mcp_tool_canceled", "mcp_tool_timeout"}:
                try:
                    self.close()
                except McpShutdownTimeout:
                    return _uncertain("MCP_SHUTDOWN_TIMEOUT")
            return result
        finally:
            if resource is not None:
                resource.close()

    def refresh_tools(self) -> tuple[mcp_types.Tool, ...]:
        try:
            return self.async_kernel.call(self._refresh_tools)
        except TimeoutError:
            raise McpUnavailable("mcp_tool_list_timeout") from None
        except (AsyncKernelClosedError, RuntimeError):
            raise McpUnavailable("mcp_tool_list_failed") from None

    def close(self) -> bool:
        hit_fault("mcp_thread_stuck")
        self.closed.set()
        service = getattr(self, "_service", None)
        if service is not None and not service.done():
            try:
                self.async_kernel.call(self._request_close)
            except (AsyncKernelClosedError, RuntimeError):
                pass
        process_group_exited = self._terminate_process_group()
        if service is not None and not service.done():
            service.wait(5)
        if service is not None and not service.done():
            service.cancel()
            service.wait(2)
        callbacks_quiescent = self._callbacks_quiescent.wait(5)
        if not process_group_exited:
            process_group_exited = self._terminate_process_group()
        if (
            (service is not None and not service.done())
            or not process_group_exited
            or not callbacks_quiescent
        ):
            resource = getattr(self, "resource", None)
            if resource is not None:
                resource.fail(
                    "MCP_CALLBACK_SHUTDOWN_TIMEOUT"
                    if not callbacks_quiescent
                    else "MCP_CANCEL_TIMEOUT"
                )
            raise McpShutdownTimeout("MCP_SHUTDOWN_TIMEOUT")
        try:
            resolved = self.runtime_root.resolve(strict=False)
            parent = self.runtime_root.parent.resolve(strict=False)
            if parent in resolved.parents:
                shutil.rmtree(resolved, ignore_errors=True)
        except OSError:
            pass
        resource = getattr(self, "resource", None)
        if resource is not None:
            resource.close()
        return True

    def _terminate_process_group(self) -> bool:
        pid_path = self.runtime_root / "server.pid"
        try:
            stat = pid_path.lstat()
            if not stat or not pid_path.is_file() or pid_path.is_symlink():
                return True
            raw_pid = pid_path.read_text(encoding="ascii")
            if len(raw_pid) > 16 or not raw_pid.isdecimal():
                return False
            process_group = int(raw_pid)
            if process_group <= 1 or process_group == os.getpgrp():
                return False
            try:
                os.killpg(process_group, signal.SIGTERM)
            except ProcessLookupError:
                return True
            for _ in range(10):
                try:
                    os.killpg(process_group, 0)
                except ProcessLookupError:
                    return True
                threading.Event().wait(0.05)
            try:
                os.killpg(process_group, signal.SIGKILL)
            except ProcessLookupError:
                return True
            for _ in range(20):
                try:
                    os.killpg(process_group, 0)
                except ProcessLookupError:
                    return True
                threading.Event().wait(0.05)
            return False
        except FileNotFoundError:
            return True
        except PermissionError:
            # The MCP stdio owner already reaped its child before this fallback
            # on restricted macOS runners; service exit is the remaining proof.
            service = getattr(self, "_service", None)
            return service is None or service.done()
        except (OSError, ValueError, UnicodeError):
            return False

    async def _serve(self, *, task_status: anyio.abc.TaskStatus[object]) -> None:
        async def message_handler(message: object) -> None:
            if isinstance(message, Exception):
                self.error_code = "mcp_stdout_pollution"
                if self._close_event is not None:
                    self._close_event.set()
                return
            root = getattr(message, "root", None)
            if isinstance(root, mcp_types.ToolListChangedNotification):
                try:
                    # The callback records an event in SQLite. It must not run
                    # synchronously on the RuntimeAsyncKernel Event Loop.
                    await anyio.to_thread.run_sync(self._run_list_changed_callback)
                except BaseException as error:
                    if isinstance(error, anyio.get_cancelled_exc_class()):
                        raise
                    # A notification callback is local bookkeeping, not MCP
                    # protocol state. Retain the service while making its local
                    # failure visible without untrusted exception data.
                    LOGGER.error(
                        "MCP tool-list change callback failed",
                        extra={"mcp_server_id": self.config.id},
                    )

        startup_scope: anyio.CancelScope | None = None
        startup_deadline: float | None = None
        startup_ready = False
        try:
            # The scope begins before any transport preparation. It remains
            # lexically around the contexts so their cleanup stays structured,
            # then explicitly loses its deadline once the service is ready.
            with anyio.fail_after(
                self.config.startup_timeout_seconds
            ) as startup_scope:
                startup_deadline = startup_scope.deadline
                parameters = await anyio.to_thread.run_sync(self._prepare_parameters)
                with open(os.devnull, "w", encoding="utf-8") as error_log:
                    async with stdio_client(parameters, errlog=error_log) as streams:
                        async with ClientSession(
                            streams[0], streams[1], message_handler=message_handler
                        ) as session:
                            await session.initialize()
                            tools = await _discover_tools(session)
                            self._session = session
                            self._session_lock = anyio.Lock()
                            self._close_event = anyio.Event()
                            self.tools = tools
                            task_status.started(tools)
                            startup_ready = True
                            startup_scope.deadline = math.inf
                            await self._close_event.wait()
        except McpUnavailable as error:
            self.error_code = str(error)
            raise
        except BaseException as error:
            cancellation_type = anyio.get_cancelled_exc_class()
            if _exception_contains(error, cancellation_type):
                if startup_scope is not None and startup_scope.cancel_called:
                    self.error_code = "mcp_startup_timeout"
                    raise McpUnavailable(self.error_code) from None
                raise
            if (
                not startup_ready
                and startup_deadline is not None
                and time.monotonic() >= startup_deadline
            ):
                self.error_code = "mcp_startup_timeout"
                raise McpUnavailable(self.error_code) from None
            self.error_code = self.error_code or (
                "mcp_startup_timeout"
                if (
                    (startup_scope is not None and startup_scope.cancel_called)
                    or (
                        startup_scope is not None
                        and startup_scope.cancelled_caught
                    )
                    or _exception_contains(error, TimeoutError)
                )
                else "mcp_protocol_error"
            )
            raise McpUnavailable(self.error_code) from None
        finally:
            self._session = None
            self._session_lock = None
            self._close_event = None

    async def _call(self, command: _Command) -> dict[str, object]:
        session = self._session
        lock = self._session_lock
        if session is None or lock is None or self.closed.is_set():
            return _uncertain(self.error_code or "mcp_connection_lost")
        async with lock:
            if self.error_code is not None:
                return _uncertain(self.error_code)
            try:
                return await _call_tool(session, command)
            except TimeoutError:
                return _uncertain("mcp_tool_timeout")
            except BaseException:
                return _uncertain(self.error_code or "mcp_connection_lost")

    async def _refresh_tools(self) -> tuple[mcp_types.Tool, ...]:
        session = self._session
        lock = self._session_lock
        if session is None or lock is None or self.closed.is_set():
            raise McpUnavailable("mcp_tool_list_failed")
        async with lock:
            with anyio.fail_after(self.config.startup_timeout_seconds):
                self.tools = await _discover_tools(session)
            return self.tools

    async def _request_close(self) -> None:
        if self._close_event is not None:
            self._close_event.set()

    def _run_list_changed_callback(self) -> None:
        with self._callback_lock:
            self._callbacks_in_flight += 1
            self._callbacks_quiescent.clear()
        try:
            self.on_list_changed()
        finally:
            with self._callback_lock:
                self._callbacks_in_flight -= 1
                if self._callbacks_in_flight == 0:
                    self._callbacks_quiescent.set()

    def _prepare_parameters(self) -> StdioServerParameters:
        self.runtime_root.mkdir(mode=0o700, parents=True)
        os.chmod(self.runtime_root, 0o700)
        executable = self.config.executable
        if "/" in executable and not Path(executable).is_absolute():
            executable = str(self.plugin_root / executable)
        arguments = list(self.config.argv)
        environment = {
            "HOME": str(self.runtime_root / "home"),
            "TMPDIR": str(self.runtime_root / "tmp"),
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "LANG": "en_US.UTF-8",
            "LC_ALL": "en_US.UTF-8",
        }
        Path(environment["HOME"]).mkdir(mode=0o700)
        Path(environment["TMPDIR"]).mkdir(mode=0o700)
        for name in self.config.env_names:
            value = os.environ.get(name)
            if value is not None:
                environment[name] = value
        if self.sandbox:
            if sys.platform != "darwin" or not Path(SANDBOX_EXECUTABLE).is_file():
                raise McpUnavailable("mcp_sandbox_unavailable")
            profile = SANDBOX_DIR / (
                "mcp_connector.sbpl"
                if self.config.permission_profile == "connector"
                else "mcp_workspace_read.sbpl"
            )
            arguments = [
                "-f", str(profile),
                f"-DPLUGIN_ROOT={self.plugin_root}",
                f"-DWORKSPACE_ROOT={self.workspace_root}",
                f"-DSANDBOX_HOME={environment['HOME']}",
                f"-DSANDBOX_TMP={environment['TMPDIR']}",
                "--", executable, *arguments,
            ]
            executable = SANDBOX_EXECUTABLE
        arguments = [
            str(LAUNCHER), str(self.runtime_root / "server.pid"),
            executable, *arguments,
        ]
        executable = sys.executable
        return StdioServerParameters(
            command=executable,
            args=arguments,
            env=environment,
            cwd=self.plugin_root,
            encoding="utf-8",
            encoding_error_handler="strict",
        )


class McpToolAdapter:
    def __init__(
        self,
        connection: McpConnection,
        remote_name: str,
        local_name: str,
        input_validator: BoundedJsonSchema,
        output_validator: BoundedJsonSchema | None,
    ) -> None:
        self.connection = connection
        self.remote_name = remote_name
        self.local_name = local_name
        self.input_validator = input_validator
        self.output_validator = output_validator

    def execute(
        self, arguments: dict[str, object], cancel: threading.Event
    ) -> dict[str, object]:
        result = dict(self.connection.call(self.remote_name, arguments, cancel))
        result["toolName"] = self.local_name
        if self.output_validator is None:
            return result
        data = result.get("data")
        structured = (
            data.get("structuredContent") if isinstance(data, dict) else None
        )
        try:
            self.output_validator.validate(structured)
        except JsonSchemaValidationError:
            return {
                "toolContractVersion": 1,
                "schemaVersion": 1,
                "toolName": self.local_name,
                "outcome": "error",
                "code": "TOOL_RESULT_CONTRACT_VIOLATION",
                "summary": "MCP structured content violated its output schema",
                "data": {},
                "sideEffectsMayExist": True,
                "reconciliationRequired": True,
            }
        return result


class _RejectedMcpAdapter:
    def execute(
        self, _arguments: dict[str, object], _cancel: threading.Event
    ) -> dict[str, object]:
        return _unavailable()


class McpManager:
    def __init__(
        self,
        plugins: PluginCatalog,
        snapshot: dict[str, object],
        workspace_root: Path,
        *,
        async_kernel: RuntimeAsyncKernel | None = None,
        sandbox: bool = True,
        resource_registry: ResourceRegistry | None = None,
    ) -> None:
        self.plugins = plugins
        self.snapshot = snapshot
        self.workspace_root = workspace_root
        self.async_kernel = async_kernel
        self.sandbox = sandbox
        self.resources = resource_registry
        self.connections: list[McpConnection] = []
        self._entries: tuple[ToolRegistryEntry, ...] = ()
        self._dirty = threading.Event()

    def start(self) -> tuple[ToolRegistryEntry, ...]:
        current = self.plugins.extension_snapshot()
        if current.get("mcpConfigHash") != self.snapshot.get("mcpConfigHash"):
            return ()
        entries: list[ToolRegistryEntry] = []
        for server in self.plugins.list_mcp_servers():
            if not server["available"]:
                continue
            plugin_id = str(server["pluginId"])
            if not _snapshot_has_plugin(self.snapshot, plugin_id, str(server["pluginHash"])):
                continue
            manifest = self.plugins.manifest(plugin_id)
            config = next(
                value for value in manifest.mcp_servers
                if value.id == server["serverId"]
            )
            if self.async_kernel is None:
                self.plugins.store.set_mcp_server_state(
                    server, consented=True, error_code="mcp_connection_lost"
                )
                continue
            connection = McpConnection(
                plugin_root=self.plugins.installed_root(plugin_id),
                runtime_root=self.plugins.store.data_directory / "extensions" / "mcp-runtime",
                workspace_root=self.workspace_root,
                config=config,
                async_kernel=self.async_kernel,
                on_list_changed=lambda plugin_id=plugin_id, server_id=config.id: self._list_changed(
                    plugin_id, server_id
                ),
                sandbox=self.sandbox,
                resource_registry=self.resources,
            )
            try:
                tools = connection.start()
            except McpUnavailable as error:
                self.plugins.store.set_mcp_server_state(
                    server, consented=True, error_code=str(error)
                )
                connection.close()
                continue
            self.connections.append(connection)
            entries.extend(_valid_tool_entries(connection, tools, server))
        self._entries = tuple(entries)
        return self._entries

    def refresh_if_changed(self) -> tuple[ToolRegistryEntry, ...] | None:
        if not self._dirty.is_set():
            return None
        self._dirty.clear()
        entries: list[ToolRegistryEntry] = []
        servers = {
            (str(value["pluginId"]), str(value["serverId"])): value
            for value in self.plugins.list_mcp_servers()
        }
        for connection in self.connections:
            server = next((value for value in servers.values() if (
                value["serverId"] == connection.config.id
                and self.plugins.installed_root(str(value["pluginId"])) == connection.plugin_root
            )), None)
            if server is None:
                continue
            try:
                tools = connection.refresh_tools()
            except McpUnavailable:
                continue
            entries.extend(_valid_tool_entries(connection, tools, server))
        self._entries = tuple(entries)
        return self._entries

    def close(self) -> bool:
        failure: McpShutdownTimeout | None = None
        for connection in self.connections:
            try:
                connection.close()
            except McpShutdownTimeout as error:
                failure = failure or error
        if failure is not None:
            raise failure
        return True

    def _list_changed(self, plugin_id: str, server_id: str) -> None:
        self._dirty.set()
        self.plugins.store.record_mcp_tool_list_changed(plugin_id, server_id)


async def _discover_tools(session: ClientSession) -> tuple[mcp_types.Tool, ...]:
    cursor: str | None = None
    cursors: set[str] = set()
    tools: list[mcp_types.Tool] = []
    schema_bytes = 0
    for _page in range(MAX_LIST_PAGES):
        page = await session.list_tools(cursor=cursor)
        for tool in page.tools:
            encoded = json.dumps(
                tool.inputSchema, ensure_ascii=False, separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            schema_bytes += len(encoded)
            tools.append(tool)
            if len(tools) > MAX_TOOLS or schema_bytes > MAX_SCHEMA_BYTES:
                raise McpUnavailable("mcp_tool_list_too_large")
        cursor = page.nextCursor
        if cursor is None:
            return tuple(tools)
        if cursor in cursors:
            raise McpUnavailable("mcp_pagination_invalid")
        cursors.add(cursor)
    raise McpUnavailable("mcp_tool_list_too_large")


async def _call_tool(
    session: ClientSession, command: _Command
) -> dict[str, object]:
    if command.cancel.is_set():
        return _uncertain("mcp_tool_canceled")
    result_holder: dict[str, mcp_types.CallToolResult] = {}
    canceled = False

    async def invoke() -> None:
        result_holder["result"] = await session.call_tool(
            command.name,
            command.arguments,
            read_timeout_seconds=timedelta(seconds=command.timeout_seconds),
        )
        group.cancel_scope.cancel()

    async def watch_cancel() -> None:
        nonlocal canceled
        while not command.cancel.is_set():
            await anyio.sleep(0.05)
        hit_fault("mcp_ignore_protocol_cancel")
        canceled = True
        group.cancel_scope.cancel()

    with anyio.fail_after(command.timeout_seconds):
        async with anyio.create_task_group() as group:
            group.start_soon(invoke)
            group.start_soon(watch_cancel)
    if canceled or command.cancel.is_set():
        return _uncertain("mcp_tool_canceled")
    result = result_holder.get("result")
    if result is None:
        return _uncertain("mcp_connection_lost")
    texts: list[str] = []
    for content in result.content:
        if not isinstance(content, mcp_types.TextContent):
            return _result("error", "mcp_content_unsupported", {})
        texts.append(content.text)
    text = "\n".join(texts)
    structured = result.structuredContent
    if structured is not None:
        try:
            validate_bounded_json_value(structured)
        except JsonSchemaValidationError:
            return _result("error", "mcp_result_too_large", {})
    data: dict[str, object] = {}
    if text:
        data["text"] = text
    if structured is not None:
        data["structuredContent"] = structured
    data["isError"] = bool(result.isError)
    if len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_RESULT_BYTES:
        return _result("error", "mcp_result_too_large", {})
    return _result("error" if result.isError else "success", "mcp_tool_error" if result.isError else "ok", data)


def _tool_entry(
    connection: McpConnection,
    tool: mcp_types.Tool,
    server: dict[str, object],
) -> ToolRegistryEntry:
    input_schema = tool.inputSchema
    input_validator = BoundedJsonSchema(input_schema)
    output_schema = tool.outputSchema
    output_validator = (
        BoundedJsonSchema(output_schema) if output_schema is not None else None
    )
    serialized = json.dumps(
        tool.model_dump(mode="json", by_alias=True, exclude_none=True),
        ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    )
    content_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    name = f"mcp__{server['serverId']}__{tool.name}"
    return ToolRegistryEntry(
        spec=ToolSpec.model_validate({
            "name": name,
            "description": tool.description or f"MCP tool {tool.name}",
            "sideEffect": "external",
            "approvalRequired": True,
            "timeoutSeconds": connection.config.tool_timeout_seconds,
            "batchPolicy": "single",
            "visibility": "deferred",
            "inputSchema": input_schema,
            "resultSchema": result_model(
                McpResultData
            ).model_json_schema(by_alias=True),
            "modelProjectionPolicy": "mcp",
        }),
        provenance=ToolProvenance.model_validate({
            "kind": "mcp",
            "sourceId": f"{server['pluginId']}:{server['serverId']}",
            "sourceVersion": server["pluginVersion"],
            "contentHash": content_hash,
            "pluginId": server["pluginId"],
            "serverId": server["serverId"],
        }),
        adapter=McpToolAdapter(
            connection, tool.name, name, input_validator, output_validator
        ),
        result_data_model=McpResultData,
        input_schema_validator=input_validator,
        output_schema_validator=output_validator,
    )


def _valid_tool_entries(
    connection: McpConnection,
    tools: tuple[mcp_types.Tool, ...],
    server: dict[str, object],
) -> list[ToolRegistryEntry]:
    entries: list[ToolRegistryEntry] = []
    for tool in tools:
        try:
            entries.append(_tool_entry(connection, tool, server))
        except (TypeError, ValueError):
            entries.append(_rejected_tool_entry(connection, tool, server))
    return entries


def _rejected_tool_entry(
    connection: McpConnection,
    tool: mcp_types.Tool,
    server: dict[str, object],
) -> ToolRegistryEntry:
    name = f"mcp__{server['serverId']}__{tool.name}"
    serialized = json.dumps(
        tool.model_dump(mode="json", by_alias=True, exclude_none=True),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return ToolRegistryEntry(
        spec=ToolSpec.model_validate({
            "name": name,
            "description": tool.description or f"MCP tool {tool.name}",
            "sideEffect": "external",
            "approvalRequired": True,
            "timeoutSeconds": connection.config.tool_timeout_seconds,
            "batchPolicy": "single",
            "visibility": "deferred",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            },
            "resultSchema": result_model(
                McpResultData
            ).model_json_schema(by_alias=True),
            "modelProjectionPolicy": "mcp",
        }),
        provenance=ToolProvenance.model_validate({
            "kind": "mcp",
            "sourceId": f"{server['pluginId']}:{server['serverId']}",
            "sourceVersion": server["pluginVersion"],
            "contentHash": hashlib.sha256(
                serialized.encode("utf-8")
            ).hexdigest(),
            "pluginId": server["pluginId"],
            "serverId": server["serverId"],
        }),
        adapter=_RejectedMcpAdapter(),
        result_data_model=McpResultData,
    )


def _snapshot_has_plugin(snapshot: dict[str, object], plugin_id: str, content_hash: str) -> bool:
    plugins = snapshot.get("plugins")
    return isinstance(plugins, list) and any(
        isinstance(value, dict)
        and value.get("id") == plugin_id
        and value.get("contentHash") == content_hash
        for value in plugins
    )


def _exception_contains(error: BaseException, kind: type[BaseException]) -> bool:
    if isinstance(error, kind):
        return True
    if isinstance(error, BaseExceptionGroup):
        return any(_exception_contains(child, kind) for child in error.exceptions)
    return False


def _result(outcome: str, code: str, data: dict[str, object]) -> dict[str, object]:
    return {
        "toolContractVersion": 1,
        "schemaVersion": 1,
        "toolName": "mcp",
        "outcome": outcome,
        "code": code,
        "summary": "MCP tool completed" if outcome == "success" else "MCP tool failed",
        "data": data,
        "sideEffectsMayExist": False,
        "reconciliationRequired": False,
    }


def _uncertain(code: str) -> dict[str, object]:
    value = _result("interrupted", code, {})
    value["sideEffectsMayExist"] = True
    value["reconciliationRequired"] = True
    return value


def _unavailable() -> dict[str, object]:
    return _result("error", "tool_unavailable", {})
