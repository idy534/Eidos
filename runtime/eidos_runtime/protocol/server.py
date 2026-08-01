from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import sys
import threading
import time
import unicodedata
import uuid
from typing import Any, BinaryIO, TextIO
from datetime import UTC, datetime

from pydantic import ValidationError

from eidos_runtime import __version__
from eidos_runtime.model.client import ModelClient, ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.model.config import (
    DEFAULT_MODEL_ID,
    SUPPORTED_MODELS,
    ModelConfigError,
    ModelConfigStore,
    model_catalog,
)
from eidos_runtime.model.pydantic_ai_client import (
    ModelClientFactory,
    ModelClientInUseError,
    ModelClientLease,
    ModelFactoryCloseError,
)
from eidos_runtime.model_gateway.auth import ModelSecretError, ModelSecretStore
from eidos_runtime.model_gateway.capabilities import resolve_model_capabilities
from eidos_runtime.model_gateway.gateway import (
    ModelGateway,
    legacy_profile_snapshot,
)
from eidos_runtime.model_gateway.models import (
    ModelProfile,
    ReasoningEffort,
    ReasoningMode,
    RetryPolicy,
    RunModelSnapshot,
    WireAPI,
)
from eidos_runtime.model_gateway.presets import PRESETS
from eidos_runtime.protocol.registry import (
    JsonObjectParams,
    JsonObjectResult,
    MethodRegistration,
    MethodRegistry,
    MethodValidationError,
)
from eidos_runtime.protocol.schemas import JsonRpcRequestDto, JsonRpcResponse
from eidos_runtime.runtime.supervisor import (
    RunCancelTimeout,
    RunReconciliationRequired,
    RunSupervisor,
    RuntimeControlState,
    RuntimeShutdownTimeout,
)
from eidos_runtime.runtime.state_machine import RuntimeLifecycle
from eidos_runtime.runtime.events import RuntimeOutputClosedError
from eidos_runtime.runtime.resource_registry import ResourceRegistryError
from eidos_runtime.runtime.async_kernel import (
    AsyncKernelCloseError,
    RuntimeAsyncKernel,
)
from eidos_runtime.runtime.fault_injection import hit_fault
from eidos_runtime.sandbox.sensitive import (
    SensitiveContentDenied,
    SensitiveScanError,
    SensitiveScanner,
)
from eidos_runtime.sandbox.seatbelt import (
    is_seatbelt_usable,
    run_seatbelt_self_test,
)
from eidos_runtime.db.storage import (
    ActiveRunError,
    InvalidCursorError,
    InvalidRunStateError,
    OperationConflictError,
    OperationInProgressError,
    ResourceNotFoundError,
    SessionActiveError,
    SessionStore,
    StorageError,
    WorkspaceBoundaryError,
)
from eidos_runtime.extensions.plugins import (
    PluginCatalog,
    PluginImportError,
)
from eidos_runtime.extensions.skills import (
    SkillCatalog,
    SkillReadError,
    deploy_system_skills,
)


MAX_MESSAGE_BYTES = 1024 * 1024
MAX_REQUEST_ID_BYTES = 128
PROTOCOL_VERSION = 1
CLIENT_REQUEST_ID = re.compile(r"client-[A-Za-z0-9._-]+")
MAX_SESSION_TITLE_BYTES = 120
TITLE_TIMEOUT_SECONDS = 10.0

logger = logging.getLogger("eidos.runtime")


def clean_session_title(value: str) -> str:
    title = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in value
    )
    title = " ".join(title.split()).strip("\"'`“”‘’ ")[:60]
    return title.encode("utf-8")[:MAX_SESSION_TITLE_BYTES].decode(
        "utf-8", errors="ignore"
    ).strip()


_PROFILE_DRAFT_KEYS = {
    "name",
    "provider",
    "baseUrl",
    "authReference",
    "wireApi",
    "modelId",
    "contextWindow",
    "maxOutputTokens",
    "reasoningMode",
    "reasoningEffort",
    "supportsTools",
    "supportsParallelTools",
    "supportsImages",
    "supportsStructuredOutput",
    "supportsPromptCache",
    "requestTimeout",
    "retryPolicy",
}


def _profile_id_param(params: object) -> str | None:
    if (
        isinstance(params, dict)
        and set(params) == {"profileId"}
        and isinstance(params.get("profileId"), str)
        and params["profileId"]
    ):
        return params["profileId"]
    return None


def _draft_auth_reference(draft: dict[str, object]) -> str:
    reference = draft.get("authReference")
    if not isinstance(reference, str):
        raise ValueError("auth reference is required")
    return reference


def _profile_from_draft(
    draft: dict[str, object],
    auth_reference: str,
    *,
    existing: ModelProfile | None = None,
) -> ModelProfile:
    if set(draft) - _PROFILE_DRAFT_KEYS:
        raise ValueError("unknown model profile field")
    provider = draft.get("provider")
    if not isinstance(provider, str) or provider not in PRESETS:
        raise ValueError("provider preset is invalid")
    preset = PRESETS[provider]
    now = datetime.now(UTC)
    retry = draft.get("retryPolicy", {})
    if not isinstance(retry, dict) or set(retry) - {
        "maxAttempts",
        "initialBackoffSeconds",
        "maxBackoffSeconds",
    }:
        raise ValueError("retry policy is invalid")
    values = {
        "id": existing.id if existing is not None else str(uuid.uuid4()),
        "name": draft.get("name"),
        "provider": provider,
        "base_url": draft.get("baseUrl", preset.default_base_url),
        "auth_reference": auth_reference,
        "wire_api": WireAPI(
            draft.get("wireApi", preset.default_wire_api.value)
        ),
        "model_id": draft.get("modelId"),
        "context_window": draft.get("contextWindow"),
        "max_output_tokens": draft.get("maxOutputTokens"),
        "reasoning_mode": ReasoningMode(
            draft.get("reasoningMode", "none")
        ),
        "reasoning_effort": (
            ReasoningEffort(draft["reasoningEffort"])
            if draft.get("reasoningEffort") is not None
            else None
        ),
        "supports_tools": draft.get("supportsTools"),
        "supports_parallel_tools": draft.get("supportsParallelTools"),
        "supports_images": draft.get("supportsImages"),
        "supports_structured_output": draft.get("supportsStructuredOutput"),
        "supports_prompt_cache": draft.get("supportsPromptCache"),
        "request_timeout": draft.get("requestTimeout", 120.0),
        "retry_policy": RetryPolicy(
            max_attempts=retry.get("maxAttempts", 3),
            initial_backoff_seconds=retry.get("initialBackoffSeconds", 0.2),
            max_backoff_seconds=retry.get("maxBackoffSeconds", 2.0),
        ),
        "created_at": existing.created_at if existing is not None else now,
        "updated_at": now,
    }
    return ModelProfile.model_validate(values)


def _public_model(value: object) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")  # type: ignore[attr-defined]
    converted = _camelize(value)
    if not isinstance(converted, dict):
        raise TypeError("public model must be an object")
    return converted


def _camelize(value: object) -> object:
    if isinstance(value, dict):
        return {
            _camel_key(str(key)): _camelize(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_camelize(item) for item in value]
    return value


def _camel_key(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


def response(request_id: str, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def protocol_error(
    request_id: str | None, code: int, message: str
) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def business_error(request_id: str, code: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": -32000,
            "message": "Request failed",
            "data": {"code": code, "retryable": False},
        },
    }


def write_message(output: TextIO, message: dict[str, Any]) -> None:
    if "id" in message and ("result" in message or "error" in message):
        message = JsonRpcResponse.model_validate(message).to_json_value()
    serialized = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
    if len(serialized.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise ValueError("outbound protocol message exceeds 1 MiB")
    output.write(serialized)
    output.write("\n")
    output.flush()


def read_bounded_line(input_stream: BinaryIO) -> tuple[bytes, bool]:
    line = input_stream.readline(MAX_MESSAGE_BYTES + 2)
    if not line:
        return b"", False
    if len(line) <= MAX_MESSAGE_BYTES + 1:
        return line, False

    while line and not line.endswith(b"\n"):
        line = input_stream.readline(MAX_MESSAGE_BYTES + 2)
    return b"", True


def valid_request_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and CLIENT_REQUEST_ID.fullmatch(value) is not None
        and len(value) <= MAX_REQUEST_ID_BYTES
    )


def valid_initialize_params(params: object) -> bool:
    if not isinstance(params, dict) or set(params) != {"client", "protocolVersion"}:
        return False
    client = params.get("client")
    return (
        isinstance(client, dict)
        and set(client) == {"name", "version"}
        and isinstance(client.get("name"), str)
        and bool(client["name"])
        and isinstance(client.get("version"), str)
        and bool(client["version"])
        and isinstance(params.get("protocolVersion"), int)
    )


class RuntimeServer:
    def __init__(
        self,
        output: TextIO,
        data_directory: Path | None = None,
        model: ModelClient | None = None,
    ) -> None:
        self.output = output
        self.initialized = False
        self.shutting_down = False
        self.store = SessionStore(data_directory)
        self.model_config = ModelConfigStore(data_directory)
        self.model = model
        self.model_factory: ModelClientFactory | None = None
        self.model_secrets = ModelSecretStore(data_directory)
        self.model_gateway: ModelGateway | None = None
        self.async_kernel: RuntimeAsyncKernel | None = None
        self.output_lock = threading.RLock()
        self.shell_available = False
        self.sensitive: SensitiveScanner | None = None
        self.plugins: PluginCatalog | None = None
        self.supervisor = RunSupervisor(
            self.store,
            self._model_lease_for_run,
            self.send,
            self._scan_text,
            lambda: self.model is not None or self.model_factory is not None,
            lambda: self.shell_available,
            lambda: self.sensitive,
            self._cleanup_extensions,
        )
        self.method_registry = self._build_method_registry()

    def handle(self, message: object) -> None:
        if not isinstance(message, dict):
            self.send(protocol_error(None, -32600, "Invalid Request"))
            return

        if self._is_server_response(message):
            self.supervisor.handle_approval_response(message)
            return

        try:
            request = JsonRpcRequestDto.model_validate({
                **message, "params": message.get("params", {})
            })
        except ValidationError:
            self.send(protocol_error(None, -32600, "Invalid Request"))
            return
        request_id = request.id
        if not valid_request_id(request_id):
            self.send(protocol_error(None, -32600, "Invalid Request"))
            return

        method = request.method
        params = request.params

        if method == "initialize":
            self.initialize(request_id, params)
            return
        if method == "runtime/shutdown":
            self.shutdown(request_id, params)
            return
        if not self.initialized:
            self.send(business_error(request_id, "RUNTIME_NOT_INITIALIZED"))
            return
        if method == "runtime/health":
            if params != {}:
                self.send(protocol_error(request_id, -32602, "Invalid params"))
            else:
                self.send(response(request_id, self.store.health()))
            return
        if self.store.health_state != "ready":
            self.send(business_error(request_id, "STORAGE_HEALTH_ONLY"))
            return
        registration = self.method_registry.get(method)
        if (
            registration is not None
            and self.supervisor.lifecycle is not RuntimeLifecycle.RUNNING
            and not registration.allowed_when_draining
        ):
            self.send(business_error(request_id, "RUNTIME_DRAINING"))
            return
        if (
            registration is not None
            and self.supervisor.control_state is RuntimeControlState.RECONFIGURING
            and not registration.allowed_during_reconfiguration
        ):
            self.send(business_error(request_id, "RUNTIME_RECONFIGURING"))
            return
        try:
            if self.method_registry.dispatch(method, request_id, params):
                return
        except MethodValidationError:
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        self.send(protocol_error(request_id, -32601, "Method not found"))

    def _build_method_registry(self) -> MethodRegistry:
        registry = MethodRegistry()
        handlers = {
            "session/create": self.create_session,
            "session/list": self.list_sessions,
            "session/read": self.read_session,
            "session/rename": self.rename_session,
            "session/delete": self.delete_session,
            "event/list": self.list_events,
            "run/start": self.start_run,
            "run/cancel": self.cancel_run,
            "model/status": self.model_status,
            "model/list": self.list_models,
            "model/configure": self.configure_model,
            "model_profile/list": self.list_model_profiles,
            "model_profile/get": self.get_model_profile,
            "model_profile/create": self.create_model_profile,
            "model_profile/update": self.update_model_profile,
            "model_profile/delete": self.delete_model_profile,
            "model_profile/list_presets": self.list_model_presets,
            "plugin/list": self.list_plugins,
            "plugin/import": self.import_plugin,
            "plugin/setEnabled": self.set_plugin_enabled,
            "plugin/remove": self.remove_plugin,
            "skill/list": self.list_skills,
            "skill/read": self.read_skill,
            "mcp/list": self.list_mcp_servers,
            "mcp/setEnabled": self.set_mcp_enabled,
            "extension/read": self.read_extensions,
            "extension/readEvents": self.read_extension_events,
        }
        draining_blocked = {
            "run/start",
            "model/configure",
            "model_profile/create",
            "model_profile/update",
            "model_profile/delete",
            "plugin/import",
            "plugin/setEnabled",
            "plugin/remove",
            "mcp/setEnabled",
        }
        reconfiguration_blocked = {"run/start", "model/configure"}
        for name, handler in handlers.items():
            registry.register(MethodRegistration(
                name=name,
                request_type=JsonObjectParams,
                response_type=JsonObjectResult,
                handler=lambda request_id, params, handler=handler: handler(
                    request_id, params.root
                ),
                allowed_when_draining=name not in draining_blocked,
                allowed_during_reconfiguration=name not in reconfiguration_blocked,
            ))
        return registry

    def initialize(self, request_id: str, params: object) -> None:
        if not valid_initialize_params(params):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        if self.initialized:
            self.send(business_error(request_id, "INVALID_STATE"))
            return
        if params["protocolVersion"] != PROTOCOL_VERSION:
            self.send(business_error(request_id, "PROTOCOL_VERSION_UNSUPPORTED"))
            return

        try:
            self.store.initialize()
            if self.store.health_state == "ready":
                self.sensitive = SensitiveScanner()
                assert self.store.data_directory is not None
                deploy_system_skills(self.store.data_directory)
                self.plugins = PluginCatalog(self.store)
                self.model_config.initialize()
                self.model_secrets.initialize()
                self.async_kernel = RuntimeAsyncKernel(
                    resource_registry=self.supervisor.resources,
                )
                self.async_kernel.start()
                self.model_gateway = ModelGateway(
                    self.model_secrets,
                    async_kernel=self.async_kernel,
                    resource_registry=self.supervisor.resources,
                )
                configured_key = self.model_config.api_key()
                if self.model is None and configured_key is not None:
                    self.model_factory = ModelClientFactory(
                        configured_key,
                        async_kernel=self.async_kernel,
                        resource_registry=self.supervisor.resources,
                    )
                self.supervisor.bind_async_kernel(self.async_kernel)
        except (
            StorageError,
            ModelConfigError,
            SensitiveScanError,
            SkillReadError,
            OSError,
            RuntimeError,
        ):
            self._close_async_kernel()
            logger.exception("Runtime storage initialization failed")
            self.send(business_error(request_id, "INTERNAL_ERROR"))
            return

        if self.store.health_state == "ready":
            seatbelt = run_seatbelt_self_test()
            if seatbelt.available:
                logger.info("Seatbelt self-test passed")
                self.shell_available = True
            else:
                logger.warning(
                    "Seatbelt self-test failed; Shell remains unavailable: %s",
                    ",".join(seatbelt.failures),
                )

        self.initialized = True
        self.send(
            response(
                request_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "runtimeVersion": __version__,
                    "capabilities": {
                        "runShell": self.shell_available,
                        "modelConfigured": (
                            self.model is not None
                            or self.model_factory is not None
                            or (
                                self.store.health_state == "ready"
                                and bool(self.store.list_model_profiles())
                            )
                        ),
                    },
                },
            )
        )
        if self.store.health_state == "ready":
            self.supervisor.events.deliver_pending()
        logger.info("Runtime initialized")
        self.supervisor.schedule_next()

    def create_session(self, request_id: str, params: object) -> None:
        if (
            not isinstance(params, dict)
            or set(params) - {"workspaceRoot", "operationId"}
            or "workspaceRoot" not in params
            or not isinstance(params.get("workspaceRoot"), str)
            or ("operationId" in params and not _is_canonical_uuid(params["operationId"]))
        ):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        try:
            session = self.store.create_session(
                params["workspaceRoot"], operation_id=params.get("operationId")
            )
        except WorkspaceBoundaryError:
            self.send(business_error(request_id, "WORKSPACE_BOUNDARY_VIOLATION"))
            return
        except OperationConflictError:
            self.send(business_error(request_id, "OPERATION_ID_REUSED"))
            return
        except OperationInProgressError:
            self.send(business_error(request_id, "OPERATION_IN_PROGRESS"))
            return
        self.send(response(request_id, session))

    def list_sessions(self, request_id: str, params: object) -> None:
        if not isinstance(params, dict) or set(params) - {"limit", "cursor"}:
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        limit = params.get("limit", 50)
        cursor = params.get("cursor")
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 200
            or (cursor is not None and not isinstance(cursor, str))
        ):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        try:
            result = self.store.list_sessions(limit=limit, cursor=cursor)
        except InvalidCursorError:
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        self.send(response(request_id, result))

    def read_session(self, request_id: str, params: object) -> None:
        if (
            not isinstance(params, dict)
            or set(params) - {"sessionId", "itemLimit", "beforeItemId"}
            or not _is_canonical_uuid(params.get("sessionId"))
        ):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        item_limit = params.get("itemLimit", 200)
        before_item_id = params.get("beforeItemId")
        if (
            not isinstance(item_limit, int)
            or isinstance(item_limit, bool)
            or not 1 <= item_limit <= 500
            or (before_item_id is not None and not _is_canonical_uuid(before_item_id))
        ):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        session = self.store.read_session(params["sessionId"])
        if session is None:
            self.send(business_error(request_id, "RESOURCE_NOT_FOUND"))
            return
        try:
            snapshot = self.store.read_session_snapshot(
                params["sessionId"],
                item_limit=item_limit,
                before_item_id=before_item_id,
            )
        except ResourceNotFoundError:
            self.send(business_error(request_id, "RESOURCE_NOT_FOUND"))
            return
        self.send(response(request_id, snapshot))

    def rename_session(self, request_id: str, params: object) -> None:
        if (
            not isinstance(params, dict)
            or set(params) - {"sessionId", "title", "operationId"}
            or not {"sessionId", "title"} <= set(params)
            or not _is_canonical_uuid(params.get("sessionId"))
            or not isinstance(params.get("title"), str)
            or ("operationId" in params and not _is_canonical_uuid(params["operationId"]))
        ):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        title = clean_session_title(params["title"])
        if not title:
            self.send(business_error(request_id, "INVALID_SESSION_TITLE"))
            return
        try:
            title = clean_session_title(self._scan_text(title))
            session = self.store.rename_session(
                params["sessionId"], title, operation_id=params.get("operationId")
            )
        except SensitiveContentDenied:
            self.send(business_error(request_id, "SENSITIVE_CONTENT_REJECTED"))
            return
        except SensitiveScanError:
            self.send(business_error(request_id, "SENSITIVE_SCAN_FAILED"))
            return
        except ResourceNotFoundError:
            self.send(business_error(request_id, "RESOURCE_NOT_FOUND"))
            return
        except OperationConflictError:
            self.send(business_error(request_id, "OPERATION_ID_REUSED"))
            return
        except OperationInProgressError:
            self.send(business_error(request_id, "OPERATION_IN_PROGRESS"))
            return
        self.send(response(request_id, session))

    def delete_session(self, request_id: str, params: object) -> None:
        if (
            not isinstance(params, dict)
            or set(params) - {"sessionId", "operationId"}
            or not _is_canonical_uuid(params.get("sessionId"))
            or ("operationId" in params and not _is_canonical_uuid(params["operationId"]))
        ):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        try:
            result = self.store.delete_session(
                params["sessionId"], operation_id=params.get("operationId")
            )
        except ResourceNotFoundError:
            self.send(business_error(request_id, "RESOURCE_NOT_FOUND"))
            return
        except SessionActiveError:
            self.send(business_error(request_id, "SESSION_HAS_ACTIVE_RUN"))
            return
        except OperationConflictError:
            self.send(business_error(request_id, "OPERATION_ID_REUSED"))
            return
        except OperationInProgressError:
            self.send(business_error(request_id, "OPERATION_IN_PROGRESS"))
            return
        self.send(response(request_id, result))

    def list_events(self, request_id: str, params: object) -> None:
        if (
            not isinstance(params, dict)
            or set(params) - {"sessionId", "afterEventId", "limit"}
            or not _is_canonical_uuid(params.get("sessionId"))
        ):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        after_event_id = params.get("afterEventId", 0)
        limit = params.get("limit", 200)
        if (
            not isinstance(after_event_id, int) or isinstance(after_event_id, bool)
            or after_event_id < 0
            or not isinstance(limit, int) or isinstance(limit, bool)
            or not 1 <= limit <= 500
        ):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        self.send(response(request_id, self.store.list_events(
            params["sessionId"], after_event_id=after_event_id, limit=limit
        )))

    def start_run(self, request_id: str, params: object) -> None:
        if (
            not isinstance(params, dict)
            or set(params) - {
                "sessionId", "userInput", "modelId", "profileId", "operationId"
            }
            or not {"sessionId", "userInput"} <= set(params)
            or not _is_canonical_uuid(params.get("sessionId"))
            or ("operationId" in params and not _is_canonical_uuid(params["operationId"]))
            or not isinstance(params.get("userInput"), str)
            or not params["userInput"].strip()
            or len(params["userInput"].encode("utf-8")) > 64 * 1024
            or ("modelId" in params and not isinstance(params["modelId"], str))
            or ("profileId" in params and not isinstance(params["profileId"], str))
            or ("modelId" in params and "profileId" in params)
        ):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        if (
            self.model is None
            and self.model_factory is None
            and not self.store.list_model_profiles()
        ):
            self.send(business_error(request_id, "INVALID_STATE"))
            return
        try:
            user_input = self._scan_text(params["userInput"])
        except SensitiveContentDenied:
            self.send(business_error(request_id, "SENSITIVE_CONTENT_REJECTED"))
            return
        except SensitiveScanError:
            self.send(business_error(request_id, "SENSITIVE_SCAN_FAILED"))
            return
        session = self.store.read_session(params["sessionId"])
        if session is None:
            self.send(business_error(request_id, "RESOURCE_NOT_FOUND"))
            return
        requested_profile_id = params.get("profileId")
        requested_model_id = params.get("modelId")
        frozen_model: RunModelSnapshot | None = None
        if (
            requested_profile_id is None
            and isinstance(requested_model_id, str)
            and self.store.get_model_profile(requested_model_id) is not None
        ):
            requested_profile_id = requested_model_id
        if isinstance(requested_profile_id, str):
            profile = self.store.get_model_profile(requested_profile_id)
            if profile is None or not self._profile_is_selectable(profile):
                self.send(business_error(request_id, "MODEL_NOT_AVAILABLE"))
                return
            frozen_model = RunModelSnapshot(
                profile=profile,
                capability=resolve_model_capabilities(profile, PRESETS[profile.provider]),
                frozen_at=datetime.now(UTC),
            )
            requested_model_id = profile.model_id
        if (
            frozen_model is None
            and requested_model_id is not None
            and requested_model_id not in SUPPORTED_MODELS
        ):
            self.send(business_error(request_id, "MODEL_NOT_AVAILABLE"))
            return
        existing_model_id = self.store.session_model_id(params["sessionId"])
        if (
            existing_model_id is not None
            and requested_model_id is not None
            and requested_model_id != existing_model_id
        ):
            self.send(business_error(request_id, "MODEL_CHANGE_NOT_ALLOWED"))
            return
        model_id = existing_model_id or requested_model_id or DEFAULT_MODEL_ID
        run_model = (
            None if frozen_model is not None else self._model_for(model_id)
        )
        extension_snapshot = (
            SkillCatalog(self.plugins).extension_snapshot()
            if self.plugins is not None else None
        )
        operation_id = params.get("operationId")
        if isinstance(operation_id, str):
            try:
                replay = self.store.operation_result(
                    operation_id,
                    "run/start",
                    {
                        "sessionId": params["sessionId"],
                        "userInput": user_input,
                        "modelId": model_id,
                        "profileId": requested_profile_id,
                        "extensionSnapshot": extension_snapshot,
                    },
                )
            except OperationConflictError:
                self.send(business_error(request_id, "OPERATION_ID_REUSED"))
                return
            except OperationInProgressError:
                self.send(business_error(request_id, "OPERATION_IN_PROGRESS"))
                return
            if isinstance(replay, dict) and isinstance(replay.get("run"), dict):
                self.send(response(request_id, replay["run"]))
                return
        needs_title = "title" not in session
        try:
            run, _user_item = self.store.enqueue_run(
                params["sessionId"], user_input,
                operation_id=params.get("operationId"),
                session_title=None,
                model_id=model_id,
                model_profile=(
                    legacy_profile_snapshot(frozen_model)
                    if frozen_model is not None
                    else getattr(run_model, "profile_snapshot", None)
                ),
                run_model_snapshot=frozen_model,
                extension_snapshot=extension_snapshot,
            )
        except ResourceNotFoundError:
            self.send(business_error(request_id, "RESOURCE_NOT_FOUND"))
            return
        except WorkspaceBoundaryError:
            self.send(business_error(request_id, "WORKSPACE_BOUNDARY_VIOLATION"))
            return
        except OperationConflictError:
            self.send(business_error(request_id, "OPERATION_ID_REUSED"))
            return
        except OperationInProgressError:
            self.send(business_error(request_id, "OPERATION_IN_PROGRESS"))
            return

        start = self.supervisor.prepare_next()
        run = self.store.read_run(str(run["id"]))
        try:
            self.send(response(request_id, run))
        except Exception:
            self.supervisor.abort(start)
            if start is not None:
                try:
                    self.store.interrupt_run(start.run_id)
                except (ResourceNotFoundError, InvalidRunStateError):
                    pass
            raise
        finally:
            self.supervisor.release(start)
        if needs_title:
            self._schedule_title_generation(
                params["sessionId"],
                user_input,
                (
                    requested_profile_id
                    if isinstance(requested_profile_id, str)
                    else model_id
                ),
            )

    def cancel_run(self, request_id: str, params: object) -> None:
        if (
            not isinstance(params, dict)
            or set(params) - {"runId", "operationId"}
            or "runId" not in params
            or not _is_canonical_uuid(params.get("runId"))
            or ("operationId" in params and not _is_canonical_uuid(params["operationId"]))
        ):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        try:
            current = self.store.read_run(params["runId"])
        except ResourceNotFoundError:
            self.send(business_error(request_id, "RESOURCE_NOT_FOUND"))
            return
        if current["status"] not in {
            "queued",
            "running",
            "waiting_approval",
            "finalizing",
            "canceled",
        }:
            self.send(business_error(request_id, "INVALID_STATE"))
            return
        try:
            current = self.supervisor.cancel_run(
                params["runId"], operation_id=params.get("operationId")
            )
        except InvalidRunStateError:
            current = self.store.read_run(params["runId"])
            if current["status"] != "canceled":
                self.send(business_error(request_id, "INVALID_STATE"))
                return
        except RunCancelTimeout:
            self.send(business_error(request_id, "RUN_CANCEL_TIMEOUT"))
            return
        except RunReconciliationRequired:
            self.send(
                business_error(request_id, "RUN_RECONCILIATION_REQUIRED")
            )
            return
        except OperationConflictError:
            self.send(business_error(request_id, "OPERATION_ID_REUSED"))
            return
        except OperationInProgressError:
            self.send(business_error(request_id, "OPERATION_IN_PROGRESS"))
            return
        self.send(response(request_id, current))

    def model_status(self, request_id: str, params: object) -> None:
        if not isinstance(params, dict) or params:
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        try:
            status = self.model_config.public_status()
        except ModelConfigError:
            self.send(business_error(request_id, "INTERNAL_ERROR"))
            return
        if self.model is not None or self.model_factory is not None:
            status["configured"] = True
        self.send(response(request_id, status))

    def list_models(self, request_id: str, params: object) -> None:
        if not isinstance(params, dict) or params:
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        legacy = model_catalog(configured=(
            self.model is not None or self.model_factory is not None
        ))
        profiles = self.store.list_model_profiles()
        options = [self._model_option(profile) for profile in profiles]
        selectable = next(
            (option["id"] for option in options if option["selectable"]),
            legacy["defaultModelId"],
        )
        self.send(response(request_id, {
            "models": options + legacy["models"],
            "defaultModelId": selectable,
        }))

    def _model_option(self, profile: ModelProfile) -> dict[str, object]:
        configured = self._profile_is_configured(profile)
        return {
            "id": profile.id,
            "provider": profile.provider,
            "displayName": profile.name,
            "configured": configured,
            "selectable": configured and self._profile_is_selectable(profile),
        }

    def _profile_is_configured(self, profile: ModelProfile) -> bool:
        try:
            self.model_secrets.resolve(profile.auth_reference)
        except (ValueError, ModelSecretError):
            return False
        return True

    def _profile_is_selectable(self, profile: ModelProfile) -> bool:
        if not self._profile_is_configured(profile):
            return False
        preset = PRESETS.get(profile.provider)
        if preset is None or not isinstance(profile.wire_api, WireAPI):
            return False
        capability = resolve_model_capabilities(profile, preset)
        return (
            capability.context_window is not None
            and capability.max_output_tokens is not None
        )

    def configure_model(self, request_id: str, params: object) -> None:
        if (
            not isinstance(params, dict)
            or set(params) != {"apiKey"}
            or not isinstance(params.get("apiKey"), str)
        ):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        if not self.supervisor.begin_reconfiguration():
            code = (
                "RUNTIME_RECONFIGURING"
                if self.supervisor.control_state
                is RuntimeControlState.RECONFIGURING
                else "RUN_ALREADY_ACTIVE"
            )
            self.send(business_error(request_id, code))
            return
        previous_factory = self.model_factory
        previous_model = self.model
        previous_key: str | None = None
        saved = False
        previous_closed = False
        candidate: ModelClientFactory | None = None
        try:
            previous_key = self.model_config.api_key()
            candidate = ModelClientFactory(
                params["apiKey"],
                async_kernel=self._required_async_kernel(),
                resource_registry=self.supervisor.resources,
            )
            if previous_factory is not None:
                previous_factory.close()
                previous_closed = True
            self.model_config.save_api_key(params["apiKey"])
            saved = True
            key = self.model_config.api_key()
            if key is None:
                raise ModelConfigError("model configuration was not saved")
            self.model = None
            self.model_factory = candidate
            candidate = None
        except ValueError:
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        except ModelClientInUseError:
            if saved:
                self.model_config.restore_api_key(previous_key)
            self.model = previous_model
            self.model_factory = previous_factory
            if candidate is not None:
                candidate.close()
            self.send(business_error(request_id, "MODEL_CLIENT_IN_USE"))
            return
        except ModelFactoryCloseError as error:
            if candidate is not None:
                candidate.close()
            self.send(
                business_error(
                    request_id, error.code
                )
            )
            return
        except (OSError, ModelConfigError):
            if saved:
                try:
                    self.model_config.restore_api_key(previous_key)
                except (OSError, ModelConfigError):
                    logger.exception("Model configuration rollback failed")
            if previous_closed and candidate is not None:
                self.model = None
                self.model_factory = candidate
                candidate = None
            else:
                self.model = previous_model
                self.model_factory = previous_factory
            if candidate is not None:
                candidate.close()
            logger.exception("Model configuration failed")
            self.send(
                business_error(
                    request_id,
                    "MODEL_CONFIG_COMMIT_FAILED"
                    if previous_closed
                    else "MODEL_RECONFIGURATION_FAILED",
                )
            )
            return
        finally:
            self.supervisor.end_reconfiguration()
        self.supervisor.schedule_next()
        self.send(response(request_id, self.model_config.public_status()))

    def list_model_profiles(self, request_id: str, params: object) -> None:
        if params != {}:
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        self.send(response(request_id, {
            "schemaVersion": 1,
            "profiles": [
                _public_model(profile)
                for profile in self.store.list_model_profiles()
            ],
        }))

    def get_model_profile(self, request_id: str, params: object) -> None:
        profile_id = _profile_id_param(params)
        if profile_id is None:
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        profile = self.store.get_model_profile(profile_id)
        if profile is None:
            self.send(business_error(request_id, "RESOURCE_NOT_FOUND"))
            return
        self.send(response(request_id, _public_model(profile)))

    def create_model_profile(self, request_id: str, params: object) -> None:
        if (
            not isinstance(params, dict)
            or set(params) - {"profile", "apiKey"}
            or not isinstance(params.get("profile"), dict)
            or (
                "apiKey" in params
                and not isinstance(params.get("apiKey"), str)
            )
        ):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        reference: str | None = None
        try:
            reference = (
                self.model_secrets.save(params["apiKey"])
                if isinstance(params.get("apiKey"), str)
                else _draft_auth_reference(params["profile"])
            )
            profile = _profile_from_draft(params["profile"], reference)
            self.model_secrets.resolve(profile.auth_reference)
            resolve_model_capabilities(profile, PRESETS[profile.provider])
            self.store.create_model_profile(profile)
        except (ValueError, ValidationError, ModelSecretError):
            if reference is not None and isinstance(params.get("apiKey"), str):
                self.model_secrets.delete(reference)
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        self.send(response(request_id, _public_model(profile)))

    def update_model_profile(self, request_id: str, params: object) -> None:
        if (
            not isinstance(params, dict)
            or set(params) - {"profileId", "profile", "apiKey"}
            or not isinstance(params.get("profileId"), str)
            or not isinstance(params.get("profile"), dict)
            or (
                "apiKey" in params
                and not isinstance(params.get("apiKey"), str)
            )
        ):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        existing = self.store.get_model_profile(params["profileId"])
        if existing is None:
            self.send(business_error(request_id, "RESOURCE_NOT_FOUND"))
            return
        reference: str | None = None
        try:
            reference = (
                self.model_secrets.save(params["apiKey"])
                if isinstance(params.get("apiKey"), str)
                else existing.auth_reference
            )
            profile = _profile_from_draft(
                params["profile"],
                reference,
                existing=existing,
            )
            self.model_secrets.resolve(profile.auth_reference)
            resolve_model_capabilities(profile, PRESETS[profile.provider])
            self.store.update_model_profile(profile)
        except (ValueError, ValidationError, ModelSecretError):
            if reference is not None and isinstance(params.get("apiKey"), str):
                self.model_secrets.delete(reference)
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        self.send(response(request_id, _public_model(profile)))

    def delete_model_profile(self, request_id: str, params: object) -> None:
        profile_id = _profile_id_param(params)
        if profile_id is None:
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        try:
            self.store.delete_model_profile(profile_id)
        except ResourceNotFoundError:
            self.send(business_error(request_id, "RESOURCE_NOT_FOUND"))
            return
        self.send(response(request_id, {"deletedProfileId": profile_id}))

    def list_model_presets(self, request_id: str, params: object) -> None:
        if params != {}:
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        self.send(response(request_id, {
            "schemaVersion": 1,
            "presets": [_public_model(preset) for preset in PRESETS.values()],
        }))

    def list_plugins(self, request_id: str, params: object) -> None:
        if not isinstance(params, dict) or params:
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        if self.plugins is None:
            self.send(business_error(request_id, "EXTENSIONS_UNAVAILABLE"))
            return
        self.send(response(request_id, {"plugins": self.plugins.list_plugins()}))

    def import_plugin(self, request_id: str, params: object) -> None:
        if (
            not isinstance(params, dict)
            or set(params) - {"sourcePath", "operationId"}
            or "sourcePath" not in params
            or not isinstance(params.get("sourcePath"), str)
            or not 1 <= len(params["sourcePath"].encode("utf-8")) <= 4096
            or not Path(params["sourcePath"]).is_absolute()
            or ("operationId" in params and not _is_canonical_uuid(params["operationId"]))
        ):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        if self.plugins is None:
            self.send(business_error(request_id, "EXTENSIONS_UNAVAILABLE"))
            return
        operation_request = {"sourcePath": params["sourcePath"]}
        operation_key = (
            str(params["operationId"])
            if isinstance(params.get("operationId"), str)
            else str(uuid.uuid4())
        )
        try:
            operation, created = self.store.accept_async_operation(
                request_id=request_id,
                operation_id=operation_key,
                scope="plugin/import",
                request=operation_request,
            )
        except OperationConflictError:
            self.send(business_error(request_id, "OPERATION_ID_REUSED"))
            return
        if not created:
            if operation.status == "completed" and operation.result is not None:
                self.send(response(request_id, operation.result))
            elif operation.status in {"accepted", "running"}:
                self.send(
                    business_error(request_id, "OPERATION_IN_PROGRESS")
                )
            else:
                self.send(
                    business_error(
                        request_id,
                        operation.error_code
                        or (
                            "ASYNC_OPERATION_INTERRUPTED"
                            if operation.status == "interrupted"
                            else "ASYNC_OPERATION_CANCELED"
                        ),
                    )
                )
            return
        scheduled = self.supervisor.start_managed_task(
            "plugin-import",
            lambda cancel: self._import_plugin_task(
                request_id, dict(params), operation.id, cancel
            ),
            operation_id=operation.id,
        )
        if not scheduled:
            self.store.cancel_async_operation(operation.id)
            self.send(business_error(request_id, "RUNTIME_DRAINING"))

    def _import_plugin_task(
        self,
        request_id: str,
        params: dict[str, object],
        async_operation_id: str,
        cancel: threading.Event,
    ) -> None:
        if cancel.is_set() or self.plugins is None:
            self.store.cancel_async_operation(async_operation_id)
            self.send(
                business_error(request_id, "ASYNC_OPERATION_CANCELED")
            )
            return
        self.store.start_async_operation(async_operation_id)
        try:
            plugin = self.plugins.import_directory(Path(params["sourcePath"]))
        except PluginImportError as error:
            if cancel.is_set():
                self.store.cancel_async_operation(async_operation_id)
                self.send(
                    business_error(request_id, "ASYNC_OPERATION_CANCELED")
                )
                return
            code = {
                "plugin_version_conflict": "PLUGIN_VERSION_CONFLICT",
                "plugin_id_conflict": "PLUGIN_ID_CONFLICT",
            }.get(str(error), "PLUGIN_IMPORT_REJECTED")
            self.store.fail_async_operation(async_operation_id, code)
            self.send(business_error(request_id, code))
            return
        except (OSError, StorageError):
            code = (
                "ASYNC_OPERATION_CANCELED"
                if cancel.is_set()
                else "PLUGIN_IMPORT_FAILED"
            )
            if cancel.is_set():
                self.store.cancel_async_operation(async_operation_id)
            else:
                self.store.fail_async_operation(
                    async_operation_id, code
                )
            self.send(business_error(request_id, code))
            return
        self.store.complete_async_operation(async_operation_id, plugin)
        self.send(response(request_id, plugin))

    def set_plugin_enabled(self, request_id: str, params: object) -> None:
        if (
            not isinstance(params, dict)
            or set(params) - {"pluginId", "enabled", "operationId"}
            or not {"pluginId", "enabled"} <= set(params)
            or not isinstance(params.get("pluginId"), str)
            or not isinstance(params.get("enabled"), bool)
            or ("operationId" in params and not _is_canonical_uuid(params["operationId"]))
        ):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        if self.plugins is None:
            self.send(business_error(request_id, "EXTENSIONS_UNAVAILABLE"))
            return
        operation_request = {"pluginId": params["pluginId"], "enabled": params["enabled"]}
        if self._extension_replay(
            request_id, params.get("operationId"), "plugin/setEnabled", operation_request
        ):
            return
        try:
            plugin = self.plugins.set_enabled(params["pluginId"], params["enabled"])
        except ResourceNotFoundError:
            self.send(business_error(request_id, "RESOURCE_NOT_FOUND"))
            return
        plugin = self._record_extension_operation(
            params.get("operationId"), "plugin/setEnabled", operation_request, plugin
        )
        self.send(response(request_id, plugin))

    def remove_plugin(self, request_id: str, params: object) -> None:
        if (
            not isinstance(params, dict)
            or set(params) - {"pluginId", "operationId"}
            or "pluginId" not in params
            or not isinstance(params.get("pluginId"), str)
            or ("operationId" in params and not _is_canonical_uuid(params["operationId"]))
        ):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        if self.plugins is None:
            self.send(business_error(request_id, "EXTENSIONS_UNAVAILABLE"))
            return
        operation_request = {"pluginId": params["pluginId"]}
        if self._extension_replay(
            request_id, params.get("operationId"), "plugin/remove", operation_request
        ):
            return
        try:
            plugin = self.plugins.remove(params["pluginId"])
        except ResourceNotFoundError:
            self.send(business_error(request_id, "RESOURCE_NOT_FOUND"))
            return
        plugin = self._record_extension_operation(
            params.get("operationId"), "plugin/remove", operation_request, plugin
        )
        self.send(response(request_id, plugin))

    def list_skills(self, request_id: str, params: object) -> None:
        if not isinstance(params, dict) or params:
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        if self.plugins is None:
            self.send(business_error(request_id, "EXTENSIONS_UNAVAILABLE"))
            return
        try:
            catalog = SkillCatalog(self.plugins)
            skills = catalog.catalog(catalog.extension_snapshot())
        except SkillReadError:
            self.send(business_error(request_id, "SKILL_CATALOG_UNAVAILABLE"))
            return
        self.send(response(request_id, {"skills": skills}))

    def read_skill(self, request_id: str, params: object) -> None:
        if (
            not isinstance(params, dict)
            or set(params) != {"qualifiedId"}
            or not isinstance(params.get("qualifiedId"), str)
        ):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        if self.plugins is None:
            self.send(business_error(request_id, "EXTENSIONS_UNAVAILABLE"))
            return
        try:
            catalog = SkillCatalog(self.plugins)
            skill = catalog.read_skill(
                catalog.extension_snapshot(), params["qualifiedId"]
            )
        except SkillReadError:
            self.send(business_error(request_id, "SKILL_UNAVAILABLE"))
            return
        self.send(response(request_id, skill))

    def list_mcp_servers(self, request_id: str, params: object) -> None:
        if not isinstance(params, dict) or params:
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        if self.plugins is None:
            self.send(business_error(request_id, "EXTENSIONS_UNAVAILABLE"))
            return
        self.send(response(request_id, {"servers": self.plugins.list_mcp_servers()}))

    def read_extensions(self, request_id: str, params: object) -> None:
        if not isinstance(params, dict) or params:
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        if self.plugins is None:
            self.send(business_error(request_id, "EXTENSIONS_UNAVAILABLE"))
            return
        waterline = self.store.extension_event_waterline()
        try:
            catalog = SkillCatalog(self.plugins)
            skills = catalog.catalog(catalog.extension_snapshot())
        except SkillReadError:
            skills = []
        self.send(response(request_id, {
            "plugins": self.plugins.list_plugins(),
            "skills": skills,
            "servers": self.plugins.list_mcp_servers(),
            "throughEventId": waterline,
        }))

    def read_extension_events(self, request_id: str, params: object) -> None:
        if (
            not isinstance(params, dict)
            or set(params) - {"afterEventId", "limit"}
        ):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        after = params.get("afterEventId", 0)
        limit = params.get("limit", 200)
        if (
            not isinstance(after, int) or isinstance(after, bool) or after < 0
            or not isinstance(limit, int) or isinstance(limit, bool)
            or not 1 <= limit <= 500
        ):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        self.send(response(request_id, self.store.list_extension_events(
            after_event_id=after, limit=limit
        )))

    def set_mcp_enabled(self, request_id: str, params: object) -> None:
        if (
            not isinstance(params, dict)
            or set(params) - {"pluginId", "serverId", "enabled", "consent", "operationId"}
            or not {"pluginId", "serverId", "enabled", "consent"} <= set(params)
            or not isinstance(params.get("pluginId"), str)
            or not isinstance(params.get("serverId"), str)
            or not isinstance(params.get("enabled"), bool)
            or params.get("consent") is not True
            or ("operationId" in params and not _is_canonical_uuid(params["operationId"]))
        ):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        if self.plugins is None:
            self.send(business_error(request_id, "EXTENSIONS_UNAVAILABLE"))
            return
        operation_request = {
            "pluginId": params["pluginId"], "serverId": params["serverId"],
            "enabled": params["enabled"], "consent": True,
        }
        if self._extension_replay(
            request_id, params.get("operationId"), "mcp/setEnabled", operation_request
        ):
            return
        try:
            server = self.plugins.set_mcp_enabled(
                params["pluginId"], params["serverId"], params["enabled"]
            )
        except ResourceNotFoundError:
            self.send(business_error(request_id, "RESOURCE_NOT_FOUND"))
            return
        except PluginImportError:
            self.send(business_error(request_id, "MCP_SERVER_DISABLED"))
            return
        server = self._record_extension_operation(
            params.get("operationId"), "mcp/setEnabled", operation_request, server
        )
        self.send(response(request_id, server))

    def _extension_replay(
        self,
        request_id: str,
        operation_id: object,
        scope: str,
        request: dict[str, object],
    ) -> bool:
        if not isinstance(operation_id, str):
            return False
        try:
            replay = self.store.operation_result(operation_id, scope, request)
        except OperationConflictError:
            self.send(business_error(request_id, "OPERATION_ID_REUSED"))
            return True
        except OperationInProgressError:
            self.send(business_error(request_id, "OPERATION_IN_PROGRESS"))
            return True
        if isinstance(replay, dict):
            self.send(response(request_id, replay))
            return True
        return False

    def _record_extension_operation(
        self,
        operation_id: object,
        scope: str,
        request: dict[str, object],
        result: dict[str, object],
    ) -> dict[str, object]:
        if not isinstance(operation_id, str):
            return result
        return self.store.record_operation_result(
            operation_id, scope, request, result
        )

    def shutdown(self, request_id: str, params: object) -> None:
        if not isinstance(params, dict) or params:
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        if not self.initialized:
            self.send(response(request_id, {}))
            self.supervisor.lifecycle = RuntimeLifecycle.CLOSED
            self.shutting_down = True
            return
        try:
            self.supervisor.shutdown()
            self._cleanup_extensions()
            self._close_model_factory()
            self._close_async_kernel()
            self.store.cancel_active_async_operations()
            self.supervisor.events.deliver_pending()
            if self.store.pending_outbox_count():
                raise ResourceRegistryError(
                    "event delivery is not quiescent"
                )
            self.supervisor.resources.ensure_empty()
        except ModelFactoryCloseError as error:
            self.send(business_error(request_id, error.code))
            return
        except (
            RuntimeShutdownTimeout,
            ModelClientInUseError,
            AsyncKernelCloseError,
            ResourceRegistryError,
        ):
            self.send(business_error(request_id, "RUNTIME_SHUTDOWN_TIMEOUT"))
            return
        self.store.close()
        self.send(response(request_id, {}))
        self.supervisor.lifecycle = RuntimeLifecycle.CLOSED
        self.shutting_down = True
        logger.info("Runtime shutdown requested")

    def send(self, message: dict[str, object]) -> None:
        with self.output_lock:
            hit_fault("jsonrpc_output_disconnect")
            if self.output.closed:
                raise RuntimeOutputClosedError("runtime output channel is closed")
            try:
                write_message(self.output, message)
            except ValueError:
                if self.output.closed:
                    raise RuntimeOutputClosedError(
                        "runtime output channel is closed"
                    ) from None
                raise

    def _scan_text(self, value: str) -> str:
        if self.sensitive is None:
            raise SensitiveScanError("sensitive scanner unavailable")
        return self.sensitive.scan_text(value).text

    @staticmethod
    def _is_server_response(message: dict[str, object]) -> bool:
        return (
            message.get("jsonrpc") == "2.0"
            and isinstance(message.get("id"), str)
            and str(message["id"]).startswith("server-")
            and "method" not in message
            and set(message) <= {"jsonrpc", "id", "result", "error"}
            and (("result" in message) != ("error" in message))
        )

    def close(self) -> None:
        if self.supervisor.lifecycle is RuntimeLifecycle.CLOSED:
            return
        if not self.initialized or self.store.health_state != "ready":
            self._close_model_factory()
            self._close_async_kernel()
            self.store.close()
            self.supervisor.lifecycle = RuntimeLifecycle.CLOSED
            return
        try:
            self.supervisor.shutdown()
            self._cleanup_extensions()
            self._close_model_factory()
            self._close_async_kernel()
            self.store.cancel_active_async_operations()
            self.supervisor.events.deliver_pending()
            if self.store.pending_outbox_count():
                raise ResourceRegistryError(
                    "event delivery is not quiescent"
                )
            self.supervisor.resources.ensure_empty()
        except (
            RuntimeShutdownTimeout,
            ModelClientInUseError,
            ModelFactoryCloseError,
            AsyncKernelCloseError,
            ResourceRegistryError,
        ):
            raise
        self.store.close()
        self.supervisor.lifecycle = RuntimeLifecycle.CLOSED

    def _model_for(self, model_id: str) -> ModelClient:
        if self.model is not None:
            return self.model
        if self.model_factory is None:
            raise ModelConfigError("model is not configured")
        return self.model_factory.client_for(model_id)

    def _model_lease_for(self, model_id: str) -> ModelClientLease:
        profile = self.store.get_model_profile(model_id)
        if profile is not None:
            if not self._profile_is_selectable(profile) or self.model_gateway is None:
                raise ModelConfigError("model profile is not available")
            return self.model_gateway.acquire_lease(RunModelSnapshot(
                profile=profile,
                capability=resolve_model_capabilities(profile, PRESETS[profile.provider]),
                frozen_at=datetime.now(UTC),
            ))
        if self.model is not None:
            return ModelClientLease(
                self.model,
                resource_registry=self.supervisor.resources,
                owner_id=model_id,
            )
        if self.model_factory is None:
            raise ModelConfigError("model is not configured")
        return self.model_factory.acquire(model_id)

    def _model_lease_for_run(self, run_id: str) -> ModelClientLease:
        try:
            snapshot = self.store.read_run_model_snapshot(run_id)
        except ResourceNotFoundError:
            run = self.store.read_run(run_id)
            return self._model_lease_for(str(run["modelId"]))
        if self.model_gateway is None:
            raise ModelConfigError("model gateway is not initialized")
        return self.model_gateway.acquire_lease(snapshot)

    def _schedule_title_generation(
        self, session_id: str, user_input: str, model_id: str
    ) -> None:
        operation, created = self.store.accept_async_operation(
            request_id=None,
            operation_id=f"title:{session_id}",
            scope="session/title",
            request={
                "sessionId": session_id,
                "userInput": user_input,
                "modelId": model_id,
            },
        )
        if not created:
            return
        scheduled = self.supervisor.start_managed_task(
            "title",
            lambda cancel: self._generate_title(
                session_id,
                user_input,
                model_id,
                operation.id,
                cancel,
            ),
            operation_id=operation.id,
        )
        if not scheduled:
            self.store.cancel_async_operation(operation.id)

    def _generate_title(
        self,
        session_id: str,
        user_input: str,
        model_id: str,
        async_operation_id: str,
        cancel: threading.Event,
    ) -> None:
        if cancel.is_set():
            self.store.cancel_async_operation(async_operation_id)
            return
        self.store.start_async_operation(async_operation_id)
        started = self.store.begin_title_generation_committed(session_id)
        self.supervisor.events.publish(started)
        lease: ModelClientLease | None = None
        failure_reason: str | None = None
        title = ""
        deadline_cancel = _TitleCancellation(
            cancel, time.monotonic() + TITLE_TIMEOUT_SECONDS
        )
        try:
            lease = self._model_lease_for(model_id)
            title = clean_session_title(
                lease.client.generate_title(user_input, deadline_cancel)
            )
            if deadline_cancel.timed_out:
                failure_reason = "title_generation_timeout"
                title = ""
            elif title:
                title = clean_session_title(self._scan_text(title))
        except Exception as error:
            if cancel.is_set():
                self.store.cancel_async_operation(async_operation_id)
                return
            failure_reason = (
                "title_generation_timeout"
                if deadline_cancel.timed_out
                else "title_generation_failed"
            )
            logger.warning(
                "Session title generation failed: %s", type(error).__name__
            )
        finally:
            if lease is not None:
                lease.close()
        if cancel.is_set():
            self.store.cancel_async_operation(async_operation_id)
            return
        title = title or clean_session_title(user_input) or "新任务"
        completed = self.store.finish_title_generation_committed(
            session_id, title, failure_reason=failure_reason
        )
        self.supervisor.events.publish(completed)
        self.store.complete_async_operation(
            async_operation_id,
            {
                "sessionId": session_id,
                "title": title,
                "failureReason": failure_reason,
            },
        )

    def _close_model_factory(self) -> None:
        factory = self.model_factory
        if factory is not None:
            factory.close()
            if self.model_factory is factory:
                self.model_factory = None

    def _required_async_kernel(self) -> RuntimeAsyncKernel:
        if self.async_kernel is None:
            raise ModelConfigError("runtime async kernel is not initialized")
        return self.async_kernel

    def _close_async_kernel(self) -> None:
        kernel = self.async_kernel
        if kernel is None:
            return
        kernel.close()
        if self.async_kernel is kernel:
            self.async_kernel = None

    def _cleanup_extensions(self) -> None:
        if self.plugins is not None:
            self.plugins.cleanup_removed()


class _TitleCancellation(threading.Event):
    def __init__(self, cancel: threading.Event, deadline: float) -> None:
        super().__init__()
        self.cancel = cancel
        self.deadline = deadline

    def is_set(self) -> bool:
        return self.cancel.is_set() or self.timed_out

    def wait(self, timeout: float | None = None) -> bool:
        if self.is_set():
            return True
        remaining = max(0.0, self.deadline - time.monotonic())
        return self.cancel.wait(
            remaining if timeout is None else min(timeout, remaining)
        ) or self.is_set()

    @property
    def timed_out(self) -> bool:
        return time.monotonic() >= self.deadline


def _is_canonical_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _model_from_environment() -> ModelClient | None:
    fixture = os.environ.get("EIDOS_FAKE_MODEL")
    if fixture == "write":
        return ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        ModelToolCall(
                            "fake-write-1",
                            "write_file",
                            {"path": "approved.txt", "content": "approved\n"},
                        ),
                    )
                ),
                ModelResponse(text="Fake model completed after the approved write."),
            ]
        )
    if fixture == "shell":
        return ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        ModelToolCall(
                            "fake-shell-1",
                            "run_shell",
                            {"command": "printf desktop-shell-ok", "timeoutSeconds": 5},
                        ),
                    )
                ),
                ModelResponse(text="Fake model completed after the approved command."),
            ]
        )
    if fixture != "1":
        return None
    return ScriptedModel(
        [
            ModelResponse(
                tool_calls=(
                    ModelToolCall("fake-read-1", "read_file", {"path": "README.md"}),
                )
            ),
            ModelResponse(text="Fake model completed after reading README.md."),
        ]
    )


def run() -> int:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    server = RuntimeServer(sys.stdout, model=_model_from_environment())

    try:
        while not server.shutting_down:
            raw_line, too_large = read_bounded_line(sys.stdin.buffer)
            if too_large:
                server.send(protocol_error(None, -32600, "Invalid Request"))
                continue
            if not raw_line:
                break

            try:
                message = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                server.send(protocol_error(None, -32700, "Parse error"))
                continue

            try:
                server.handle(message)
            except Exception:
                logger.exception("Runtime request failed")
                request_id = message.get("id") if isinstance(message, dict) else None
                if valid_request_id(request_id):
                    server.send(business_error(request_id, "INTERNAL_ERROR"))
    finally:
        server.close()

    return 0
