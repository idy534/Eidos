from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
import hashlib
import json
import logging
import os
from pathlib import Path
import queue
import signal
import shutil
import sys
import threading
import uuid
from typing import Callable

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp import types as mcp_types

from eidos_runtime.extensions.contracts import McpServerConfigV1
from eidos_runtime.extensions.plugins import PluginCatalog
from eidos_runtime.tools.registry import ToolProvenance, ToolRegistryEntry, ToolSpec


MAX_LIST_PAGES = 32
MAX_TOOLS = 512
MAX_SCHEMA_BYTES = 256 * 1024
MAX_RESULT_BYTES = 256 * 1024
MAX_STRUCTURED_ITEMS = 4096
SANDBOX_EXECUTABLE = "/usr/bin/sandbox-exec"
SANDBOX_DIR = Path(__file__).resolve().parents[1] / "sandbox"
LAUNCHER = Path(__file__).with_name("mcp_launcher.py")

# The SDK includes the polluted stdout line in parser exception logs. Eidos maps
# that condition to a closed code and never logs the untrusted protocol bytes.
logging.getLogger("mcp.client.stdio").setLevel(logging.CRITICAL)


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
    completed: threading.Event = field(default_factory=threading.Event)
    result: dict[str, object] | None = None


class McpConnection:
    def __init__(
        self,
        *,
        plugin_root: Path,
        runtime_root: Path,
        workspace_root: Path,
        config: McpServerConfigV1,
        on_list_changed: Callable[[], None],
        sandbox: bool = True,
    ) -> None:
        self.plugin_root = plugin_root
        self.workspace_root = workspace_root
        self.config = config
        self.on_list_changed = on_list_changed
        self.sandbox = sandbox
        self.ready = threading.Event()
        self.closed = threading.Event()
        self.commands: queue.Queue[_Command | None] = queue.Queue()
        self.tools: tuple[mcp_types.Tool, ...] = ()
        self.error_code: str | None = None
        self.runtime_root = runtime_root / uuid.uuid4().hex
        self.thread = threading.Thread(
            target=self._thread_main,
            name=f"eidos-mcp-{config.id}",
            daemon=False,
        )

    def start(self) -> tuple[mcp_types.Tool, ...]:
        self.runtime_root.mkdir(mode=0o700, parents=True)
        os.chmod(self.runtime_root, 0o700)
        self.thread.start()
        if not self.ready.wait(self.config.startup_timeout_seconds + 1):
            self.error_code = "mcp_startup_timeout"
            self.close()
        if self.error_code is not None:
            raise McpUnavailable(self.error_code)
        return self.tools

    def call(
        self,
        name: str,
        arguments: dict[str, object],
        cancel: threading.Event,
    ) -> dict[str, object]:
        if self.error_code is not None or not self.thread.is_alive():
            return _unavailable()
        command = _Command(name, arguments, self.config.tool_timeout_seconds, cancel)
        self.commands.put(command)
        while not command.completed.wait(0.05):
            if cancel.is_set():
                continue
            if not self.thread.is_alive():
                return _uncertain("mcp_connection_lost")
        if self.error_code == "mcp_stdout_pollution":
            return _uncertain("mcp_stdout_pollution")
        result = command.result or _uncertain("mcp_connection_lost")
        if result.get("code") in {"mcp_tool_canceled", "mcp_tool_timeout"}:
            try:
                self.close()
            except McpShutdownTimeout:
                return _uncertain("MCP_SHUTDOWN_TIMEOUT")
        return result

    def refresh_tools(self) -> tuple[mcp_types.Tool, ...]:
        command = _Command("\x00list", {}, self.config.startup_timeout_seconds, threading.Event())
        self.commands.put(command)
        if not command.completed.wait(self.config.startup_timeout_seconds + 1):
            raise McpUnavailable("mcp_tool_list_timeout")
        if command.result is None or command.result.get("outcome") != "success":
            raise McpUnavailable("mcp_tool_list_failed")
        return self.tools

    def close(self) -> bool:
        if not self.closed.is_set():
            self.closed.set()
            self.commands.put(None)
        process_group_exited = self._terminate_process_group()
        if self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(timeout=5)
        if not process_group_exited:
            process_group_exited = self._terminate_process_group()
        if self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)
        if self.thread.is_alive() or not process_group_exited:
            raise McpShutdownTimeout("MCP_SHUTDOWN_TIMEOUT")
        try:
            resolved = self.runtime_root.resolve(strict=False)
            parent = self.runtime_root.parent.resolve(strict=False)
            if parent in resolved.parents:
                shutil.rmtree(resolved, ignore_errors=True)
        except OSError:
            pass
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
            # on restricted macOS runners; the thread exit is the remaining proof.
            return not self.thread.is_alive()
        except (OSError, ValueError, UnicodeError):
            return False

    def _thread_main(self) -> None:
        try:
            anyio.run(self._serve)
        except BaseException as error:
            self.error_code = self.error_code or (
                str(error) if isinstance(error, McpUnavailable)
                else "mcp_connection_failed"
            )
            self.ready.set()
            while True:
                try:
                    command = self.commands.get_nowait()
                except queue.Empty:
                    break
                if command is not None:
                    command.result = _uncertain(self.error_code)
                    command.completed.set()

    async def _serve(self) -> None:
        parameters = self._parameters()

        async def message_handler(message: object) -> None:
            if isinstance(message, Exception):
                self.error_code = "mcp_stdout_pollution"
                return
            root = getattr(message, "root", None)
            if isinstance(root, mcp_types.ToolListChangedNotification):
                self.on_list_changed()

        with open(os.devnull, "w", encoding="utf-8") as error_log:
            try:
                with anyio.fail_after(self.config.startup_timeout_seconds):
                    async with stdio_client(parameters, errlog=error_log) as streams:
                        async with ClientSession(
                            streams[0], streams[1], message_handler=message_handler
                        ) as session:
                            await session.initialize()
                            self.tools = await _discover_tools(session)
                            self.ready.set()
                            await self._command_loop(session)
            except TimeoutError:
                self.error_code = "mcp_startup_timeout"
            except McpUnavailable as error:
                self.error_code = str(error)
            except BaseException:
                self.error_code = "mcp_protocol_error"
            finally:
                self.ready.set()

    async def _command_loop(self, session: ClientSession) -> None:
        while not self.closed.is_set():
            command = await anyio.to_thread.run_sync(
                self._next_command, abandon_on_cancel=True
            )
            if command is None:
                return
            try:
                if command.name == "\x00list":
                    with anyio.fail_after(command.timeout_seconds):
                        self.tools = await _discover_tools(session)
                    command.result = _result("success", "ok", {})
                else:
                    command.result = await _call_tool(session, command)
            except TimeoutError:
                command.result = _uncertain("mcp_tool_timeout")
            except BaseException:
                command.result = _uncertain("mcp_connection_lost")
            finally:
                command.completed.set()

    def _next_command(self) -> _Command | None:
        while not self.closed.is_set():
            try:
                return self.commands.get(timeout=0.1)
            except queue.Empty:
                continue
        return None

    def _parameters(self) -> StdioServerParameters:
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
    execution_kind = "external"

    def __init__(
        self, connection: McpConnection, remote_name: str, input_schema: dict[str, object]
    ) -> None:
        self.connection = connection
        self.remote_name = remote_name
        self.input_schema = input_schema

    def effective_arguments(self, arguments: object) -> dict[str, object] | None:
        return _effective_arguments(self.input_schema, arguments)

    def execute(
        self, arguments: dict[str, object], cancel: threading.Event
    ) -> dict[str, object]:
        return self.connection.call(self.remote_name, arguments, cancel)


class McpManager:
    def __init__(
        self,
        plugins: PluginCatalog,
        snapshot: dict[str, object],
        workspace_root: Path,
        *,
        sandbox: bool = True,
    ) -> None:
        self.plugins = plugins
        self.snapshot = snapshot
        self.workspace_root = workspace_root
        self.sandbox = sandbox
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
            connection = McpConnection(
                plugin_root=self.plugins.installed_root(plugin_id),
                runtime_root=self.plugins.store.data_directory / "extensions" / "mcp-runtime",
                workspace_root=self.workspace_root,
                config=config,
                on_list_changed=lambda plugin_id=plugin_id, server_id=config.id: self._list_changed(
                    plugin_id, server_id
                ),
                sandbox=self.sandbox,
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
    if structured is not None and not _bounded_json(structured):
        return _result("error", "mcp_result_too_large", {})
    data: dict[str, object] = {}
    if text:
        data["text"] = text
    if structured is not None:
        data["structuredContent"] = structured
    if len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_RESULT_BYTES:
        return _result("error", "mcp_result_too_large", {})
    return _result("error" if result.isError else "success", "mcp_tool_error" if result.isError else "ok", data)


def _tool_entry(
    connection: McpConnection,
    tool: mcp_types.Tool,
    server: dict[str, object],
) -> ToolRegistryEntry:
    input_schema = tool.inputSchema
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
            "resultSchema": {
                "type": "object", "properties": {}, "required": [],
                "additionalProperties": False,
            },
        }),
        provenance=ToolProvenance.model_validate({
            "kind": "mcp",
            "sourceId": f"{server['pluginId']}:{server['serverId']}",
            "sourceVersion": server["pluginVersion"],
            "contentHash": content_hash,
            "pluginId": server["pluginId"],
            "serverId": server["serverId"],
        }),
        adapter=McpToolAdapter(connection, tool.name, input_schema),
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
            continue
    return entries


def _effective_arguments(
    schema: dict[str, object], arguments: object
) -> dict[str, object] | None:
    if not isinstance(arguments, dict) or schema.get("type") != "object":
        return None
    properties = schema.get("properties")
    required = schema.get("required", [])
    if (
        not isinstance(properties, dict)
        or schema.get("additionalProperties") is not False
        or not isinstance(required, list)
        or not set(arguments) <= set(properties)
        or not set(required) <= set(arguments)
    ):
        return None
    effective = dict(arguments)
    for key, child in properties.items():
        if key not in effective and isinstance(child, dict) and "default" in child:
            effective[key] = child["default"]
    if not set(required) <= set(effective):
        return None
    return effective if all(
        _schema_value(properties[key], value) for key, value in effective.items()
    ) else None


def _schema_value(schema: object, value: object) -> bool:
    if not isinstance(schema, dict):
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if "const" in schema and value != schema["const"]:
        return False
    kind = schema.get("type")
    if kind == "string":
        return isinstance(value, str) and (
            "minLength" not in schema or len(value) >= schema["minLength"]
        ) and ("maxLength" not in schema or len(value) <= schema["maxLength"])
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "array":
        return isinstance(value, list) and all(
            _schema_value(schema.get("items"), item) for item in value
        )
    if kind == "object":
        return _effective_arguments(schema, value) is not None
    return False


def _bounded_json(value: object, depth: int = 0, count: list[int] | None = None) -> bool:
    count = count or [0]
    count[0] += 1
    if depth > 16 or count[0] > MAX_STRUCTURED_ITEMS:
        return False
    if value is None or isinstance(value, (str, bool, int, float)):
        return not isinstance(value, float) or (value == value and abs(value) != float("inf"))
    if isinstance(value, list):
        return all(_bounded_json(item, depth + 1, count) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _bounded_json(item, depth + 1, count)
            for key, item in value.items()
        )
    return False


def _snapshot_has_plugin(snapshot: dict[str, object], plugin_id: str, content_hash: str) -> bool:
    plugins = snapshot.get("plugins")
    return isinstance(plugins, list) and any(
        isinstance(value, dict)
        and value.get("id") == plugin_id
        and value.get("contentHash") == content_hash
        for value in plugins
    )


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
