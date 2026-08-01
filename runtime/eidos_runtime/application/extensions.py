"""Typed Plugin, Skill and MCP configuration use cases.

The application owns extension operation replay and durable async-operation
state.  It returns business values only; the protocol layer remains responsible
for scheduling a deferred import and producing JSON-RPC envelopes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Callable
from typing import Literal, Mapping, Protocol, TypeVar
import uuid

from pydantic import JsonValue, ValidationError

from eidos_runtime.application.errors import (
    ApplicationError,
    ApplicationInvalidParamsError,
)
from eidos_runtime.db.errors import (
    OperationConflictError,
    OperationInProgressError,
    ResourceNotFoundError,
    StorageError,
)
from eidos_runtime.db.repositories.async_operations import AsyncOperation
from eidos_runtime.extensions.plugins import PluginCatalog, PluginImportError
from eidos_runtime.extensions.skills import SkillCatalog, SkillReadError
from eidos_runtime.models import EidosFrozenStrictModel


ExtensionResultT = TypeVar("ExtensionResultT", "PluginRecord", "McpServerRecord")
ValidatedResultT = TypeVar("ValidatedResultT", bound=EidosFrozenStrictModel)


class ExtensionStore(Protocol):
    """Durable extension and idempotency operations used by this service."""

    def accept_async_operation(
        self,
        *,
        request_id: str | None,
        operation_id: str,
        scope: str,
        request: dict[str, object],
    ) -> tuple[AsyncOperation, bool]: ...

    def start_async_operation(self, operation_id: str) -> AsyncOperation: ...

    def complete_async_operation(
        self, operation_id: str, result: dict[str, object]
    ) -> AsyncOperation: ...

    def fail_async_operation(
        self, operation_id: str, error_code: str
    ) -> AsyncOperation: ...

    def cancel_async_operation(self, operation_id: str) -> AsyncOperation: ...

    def operation_result(
        self, operation_id: str, scope: str, request: dict[str, object]
    ) -> object | None: ...

    def record_operation_result(
        self,
        operation_id: str,
        scope: str,
        request: dict[str, object],
        result: dict[str, object],
    ) -> dict[str, object]: ...

    def extension_event_waterline(self) -> int: ...

    def list_extension_events(
        self, *, after_event_id: int = 0, limit: int = 200
    ) -> dict[str, object]: ...


class CancellationSignal(Protocol):
    def is_set(self) -> bool: ...


class PluginRecord(EidosFrozenStrictModel):
    schema_version: Literal[1] = 1
    id: str
    name: str
    version: str
    description: str
    content_hash: str
    enabled: bool
    status: Literal["installed", "removed"]
    installed_at: int
    updated_at: int

    @classmethod
    def from_wire(cls, value: object) -> "PluginRecord":
        return _validated(cls, value, "plugin result is invalid")


class SkillMetadata(EidosFrozenStrictModel):
    schema_version: Literal[1] = 1
    qualified_id: str
    name: str
    description: str
    plugin_id: str
    plugin_version: str
    plugin_hash: str
    content_hash: str

    @classmethod
    def from_wire(cls, value: object) -> "SkillMetadata":
        return _validated(cls, value, "skill metadata is invalid")


class SkillSource(EidosFrozenStrictModel):
    plugin_id: str
    plugin_version: str
    plugin_hash: str


class SkillContent(EidosFrozenStrictModel):
    qualified_id: str
    content: str
    content_hash: str
    source: SkillSource

    @classmethod
    def from_wire(cls, value: object) -> "SkillContent":
        return _validated(cls, value, "skill content is invalid")


class McpServerRecord(EidosFrozenStrictModel):
    schema_version: Literal[1] = 1
    plugin_id: str
    plugin_version: str
    plugin_hash: str
    server_id: str
    executable: str
    argv: tuple[str, ...]
    env_names: tuple[str, ...]
    permission_profile: Literal["connector", "workspace_read"]
    startup_timeout_seconds: int
    tool_timeout_seconds: int
    declared_enabled: bool
    consented: bool
    available: bool
    error_code: str | None = None
    updated_at: int

    @classmethod
    def from_wire(cls, value: object) -> "McpServerRecord":
        if not isinstance(value, Mapping):
            raise ApplicationError("INTERNAL_ERROR", "MCP server result is invalid")
        normalized = dict(value)
        for field_name in ("argv", "envNames"):
            raw = normalized.get(field_name)
            if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
                raise ApplicationError(
                    "INTERNAL_ERROR", "MCP server result is invalid"
                )
            normalized[field_name] = tuple(raw)
        return _validated(cls, normalized, "MCP server result is invalid")


class ExtensionEvent(EidosFrozenStrictModel):
    event_contract_version: Literal[1]
    event_id: int
    event_type: str
    occurred_at: int
    session_id: str | None = None
    run_id: str | None = None
    payload: dict[str, JsonValue]

    @classmethod
    def from_wire(cls, value: object) -> "ExtensionEvent":
        return _validated(cls, value, "extension event is invalid")


class PluginList(EidosFrozenStrictModel):
    plugins: tuple[PluginRecord, ...]


class SkillList(EidosFrozenStrictModel):
    skills: tuple[SkillMetadata, ...]


class McpServerList(EidosFrozenStrictModel):
    servers: tuple[McpServerRecord, ...]


class ExtensionRead(EidosFrozenStrictModel):
    plugins: tuple[PluginRecord, ...]
    skills: tuple[SkillMetadata, ...]
    servers: tuple[McpServerRecord, ...]
    through_event_id: int


class ExtensionEvents(EidosFrozenStrictModel):
    items: tuple[ExtensionEvent, ...]
    has_more: bool
    through_event_id: int


@dataclass(frozen=True)
class PluginImportCompleted:
    """A deferred import completed and persisted its plugin record."""

    plugin: PluginRecord


@dataclass(frozen=True)
class PluginImportFailure:
    """A deferred import reached a stable persisted failure state."""

    code: str


@dataclass(frozen=True)
class DeferredPluginImport:
    """Accepted import work that a Runtime-managed task must execute later."""

    async_operation_id: str
    operation_id: str
    request_id: str | None
    source_path: Path
    _application: "ExtensionApplication" = field(repr=False, compare=False)

    def run(
        self, cancel: CancellationSignal
    ) -> PluginImportCompleted | PluginImportFailure:
        return self._application._run_plugin_import(self, cancel)

    def cancel_before_start(self) -> PluginImportFailure:
        self._application._store.cancel_async_operation(self.async_operation_id)
        return PluginImportFailure("RUNTIME_DRAINING")


PluginImportResult = DeferredPluginImport | PluginImportCompleted


class ExtensionApplication:
    """Coordinates durable Plugin, Skill and MCP configuration use cases."""

    def __init__(
        self,
        *,
        store: ExtensionStore,
        plugins: PluginCatalog | None | Callable[[], PluginCatalog | None],
    ) -> None:
        self._store = store
        self._plugins = plugins

    def list_plugins(self) -> PluginList:
        return PluginList(
            plugins=tuple(
                PluginRecord.from_wire(plugin)
                for plugin in self._plugins_or_error().list_plugins()
            )
        )

    def prepare_plugin_import(
        self,
        *,
        source_path: Path,
        operation_id: str | None,
        request_id: str | None,
    ) -> PluginImportResult:
        plugins = self._plugins_or_error()
        del plugins  # validates availability before accepting durable work
        if not source_path.is_absolute():
            raise _invalid_params("plugin source path must be absolute")
        operation_key = operation_id or str(uuid.uuid4())
        request = {"sourcePath": str(source_path)}
        try:
            operation, created = self._store.accept_async_operation(
                request_id=request_id,
                operation_id=operation_key,
                scope="plugin/import",
                request=request,
            )
        except OperationConflictError as error:
            raise ApplicationError("OPERATION_ID_REUSED") from error
        if not created:
            return self._replay_plugin_import(operation)
        return DeferredPluginImport(
            async_operation_id=operation.id,
            operation_id=operation.operation_id,
            request_id=operation.request_id,
            source_path=source_path,
            _application=self,
        )

    def set_plugin_enabled(
        self, *, plugin_id: str, enabled: bool, operation_id: str | None = None
    ) -> PluginRecord:
        plugins = self._plugins_or_error()
        request = {"pluginId": plugin_id, "enabled": enabled}
        replay = self._extension_replay(
            operation_id, "plugin/setEnabled", request, PluginRecord
        )
        if replay is not None:
            return replay
        try:
            plugin = PluginRecord.from_wire(plugins.set_enabled(plugin_id, enabled))
        except ResourceNotFoundError as error:
            raise ApplicationError("RESOURCE_NOT_FOUND") from error
        return self._record_extension_operation(
            operation_id, "plugin/setEnabled", request, plugin
        )

    def remove_plugin(
        self, *, plugin_id: str, operation_id: str | None = None
    ) -> PluginRecord:
        plugins = self._plugins_or_error()
        request = {"pluginId": plugin_id}
        replay = self._extension_replay(
            operation_id, "plugin/remove", request, PluginRecord
        )
        if replay is not None:
            return replay
        try:
            plugin = PluginRecord.from_wire(plugins.remove(plugin_id))
        except ResourceNotFoundError as error:
            raise ApplicationError("RESOURCE_NOT_FOUND") from error
        return self._record_extension_operation(
            operation_id, "plugin/remove", request, plugin
        )

    def list_skills(self) -> SkillList:
        catalog = self._skill_catalog()
        try:
            skills = tuple(
                SkillMetadata.from_wire(skill)
                for skill in catalog.catalog(catalog.extension_snapshot())
            )
        except SkillReadError as error:
            raise ApplicationError("SKILL_CATALOG_UNAVAILABLE") from error
        return SkillList(skills=skills)

    def read_skill(self, *, qualified_id: str) -> SkillContent:
        catalog = self._skill_catalog()
        try:
            return SkillContent.from_wire(
                catalog.read_skill(catalog.extension_snapshot(), qualified_id)
            )
        except SkillReadError as error:
            raise ApplicationError("SKILL_UNAVAILABLE") from error

    def list_mcp_servers(self) -> McpServerList:
        return McpServerList(
            servers=tuple(
                McpServerRecord.from_wire(server)
                for server in self._plugins_or_error().list_mcp_servers()
            )
        )

    def set_mcp_enabled(
        self,
        *,
        plugin_id: str,
        server_id: str,
        enabled: bool,
        consent: bool,
        operation_id: str | None = None,
    ) -> McpServerRecord:
        if consent is not True:
            raise _invalid_params("MCP consent is required")
        plugins = self._plugins_or_error()
        request = {
            "pluginId": plugin_id,
            "serverId": server_id,
            "enabled": enabled,
            "consent": True,
        }
        replay = self._extension_replay(
            operation_id, "mcp/setEnabled", request, McpServerRecord
        )
        if replay is not None:
            return replay
        try:
            server = McpServerRecord.from_wire(
                plugins.set_mcp_enabled(plugin_id, server_id, enabled)
            )
        except ResourceNotFoundError as error:
            raise ApplicationError("RESOURCE_NOT_FOUND") from error
        except PluginImportError as error:
            raise ApplicationError("MCP_SERVER_DISABLED") from error
        return self._record_extension_operation(
            operation_id, "mcp/setEnabled", request, server
        )

    def read_extensions(self) -> ExtensionRead:
        plugins = self._plugins_or_error()
        try:
            catalog = SkillCatalog(plugins)
            skills = tuple(
                SkillMetadata.from_wire(skill)
                for skill in catalog.catalog(catalog.extension_snapshot())
            )
        except SkillReadError:
            skills = ()
        return ExtensionRead(
            plugins=tuple(
                PluginRecord.from_wire(plugin) for plugin in plugins.list_plugins()
            ),
            skills=skills,
            servers=tuple(
                McpServerRecord.from_wire(server)
                for server in plugins.list_mcp_servers()
            ),
            through_event_id=self._store.extension_event_waterline(),
        )

    def read_extension_events(
        self, *, after_event_id: int = 0, limit: int = 200
    ) -> ExtensionEvents:
        if (
            not isinstance(after_event_id, int)
            or isinstance(after_event_id, bool)
            or after_event_id < 0
        ):
            raise _invalid_params("after event ID is invalid")
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 500
        ):
            raise _invalid_params("event limit is invalid")
        result = self._store.list_extension_events(
            after_event_id=after_event_id, limit=limit
        )
        try:
            items = result["items"]
            has_more = result["hasMore"]
            through_event_id = result["throughEventId"]
        except (KeyError, TypeError) as error:
            raise ApplicationError("INTERNAL_ERROR", "extension events are invalid") from error
        if not isinstance(items, list) or not isinstance(has_more, bool):
            raise ApplicationError("INTERNAL_ERROR", "extension events are invalid")
        return ExtensionEvents(
            items=tuple(ExtensionEvent.from_wire(item) for item in items),
            has_more=has_more,
            through_event_id=through_event_id,
        )

    def _run_plugin_import(
        self,
        deferred: DeferredPluginImport,
        cancel: CancellationSignal,
    ) -> PluginImportCompleted | PluginImportFailure:
        if cancel.is_set():
            self._store.cancel_async_operation(deferred.async_operation_id)
            return PluginImportFailure("ASYNC_OPERATION_CANCELED")
        plugins = self._current_plugins()
        if plugins is None:
            self._store.cancel_async_operation(deferred.async_operation_id)
            return PluginImportFailure("ASYNC_OPERATION_CANCELED")
        self._store.start_async_operation(deferred.async_operation_id)
        try:
            plugin = PluginRecord.from_wire(plugins.import_directory(deferred.source_path))
        except PluginImportError as error:
            if cancel.is_set():
                self._store.cancel_async_operation(deferred.async_operation_id)
                return PluginImportFailure("ASYNC_OPERATION_CANCELED")
            code = {
                "plugin_version_conflict": "PLUGIN_VERSION_CONFLICT",
                "plugin_id_conflict": "PLUGIN_ID_CONFLICT",
            }.get(str(error), "PLUGIN_IMPORT_REJECTED")
            self._store.fail_async_operation(deferred.async_operation_id, code)
            return PluginImportFailure(code)
        except (OSError, StorageError):
            code = "ASYNC_OPERATION_CANCELED" if cancel.is_set() else "PLUGIN_IMPORT_FAILED"
            if cancel.is_set():
                self._store.cancel_async_operation(deferred.async_operation_id)
            else:
                self._store.fail_async_operation(deferred.async_operation_id, code)
            return PluginImportFailure(code)
        self._store.complete_async_operation(
            deferred.async_operation_id, plugin.to_wire_dict()
        )
        return PluginImportCompleted(plugin)

    def _replay_plugin_import(
        self, operation: AsyncOperation
    ) -> PluginImportCompleted:
        if operation.status == "completed" and operation.result is not None:
            return PluginImportCompleted(PluginRecord.from_wire(operation.result))
        if operation.status in {"accepted", "running"}:
            raise ApplicationError("OPERATION_IN_PROGRESS")
        code = operation.error_code or (
            "ASYNC_OPERATION_INTERRUPTED"
            if operation.status == "interrupted"
            else "ASYNC_OPERATION_CANCELED"
        )
        raise ApplicationError(code)

    def _extension_replay(
        self,
        operation_id: str | None,
        scope: str,
        request: dict[str, object],
        result_type: type[ExtensionResultT],
    ) -> ExtensionResultT | None:
        if operation_id is None:
            return None
        try:
            replay = self._store.operation_result(operation_id, scope, request)
        except OperationConflictError as error:
            raise ApplicationError("OPERATION_ID_REUSED") from error
        except OperationInProgressError as error:
            raise ApplicationError("OPERATION_IN_PROGRESS") from error
        if replay is None:
            return None
        return result_type.from_wire(replay)

    def _record_extension_operation(
        self,
        operation_id: str | None,
        scope: str,
        request: dict[str, object],
        result: ExtensionResultT,
    ) -> ExtensionResultT:
        if operation_id is None:
            return result
        recorded = self._store.record_operation_result(
            operation_id, scope, request, result.to_wire_dict()
        )
        return type(result).from_wire(recorded)

    def _plugins_or_error(self) -> PluginCatalog:
        plugins = self._current_plugins()
        if plugins is None:
            raise ApplicationError("EXTENSIONS_UNAVAILABLE")
        return plugins

    def _current_plugins(self) -> PluginCatalog | None:
        if callable(self._plugins):
            return self._plugins()
        return self._plugins

    def _skill_catalog(self) -> SkillCatalog:
        return SkillCatalog(self._plugins_or_error())


def _validated(
    result_type: type[ValidatedResultT], value: object, message: str
) -> ValidatedResultT:
    try:
        return result_type.model_validate(value)
    except ValidationError as error:
        raise ApplicationError("INTERNAL_ERROR", message) from error


def _invalid_params(message: str) -> ApplicationInvalidParamsError:
    return ApplicationInvalidParamsError("INVALID_PARAMS", message)


__all__ = [
    "CancellationSignal",
    "DeferredPluginImport",
    "ExtensionApplication",
    "ExtensionEvents",
    "ExtensionRead",
    "ExtensionStore",
    "McpServerList",
    "McpServerRecord",
    "PluginImportCompleted",
    "PluginImportFailure",
    "PluginImportResult",
    "PluginList",
    "PluginRecord",
    "SkillContent",
    "SkillList",
    "SkillMetadata",
]
