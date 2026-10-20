from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, BinaryIO, TextIO

from pydantic import BaseModel, ValidationError

from eidos_runtime import __version__
from eidos_runtime.application.channels import ChannelApplication
from eidos_runtime.domain.channels import ChannelError
from eidos_runtime.protocol.channels import ChannelReadRequest, ChannelSaveRequest, ChannelResponse
from eidos_runtime.infrastructure.channel_secrets import ChannelSecrets
from eidos_runtime.persistence.channels import ChannelRepository
from eidos_runtime.runtime.channel_service import ChannelService
from eidos_runtime.application.errors import (
    ApplicationError,
    ApplicationInvalidParamsError,
)
from eidos_runtime.application.approvals import (
    ApprovalApplication,
    ApprovalDecision as ApplicationApprovalDecision,
)
from eidos_runtime.application.context import ContextApplication
from eidos_runtime.domain.context_management import ContextDetails, ContextCompactionRecord
from eidos_runtime.persistence.context_management import ContextManagementRepository
from eidos_runtime.application.checkpoints import CheckpointApplication
from eidos_runtime.application.extensions import (
    DeferredPluginImport,
    ExtensionApplication,
    PluginImportCompleted,
    PluginImportFailure,
)
from eidos_runtime.application.models import ModelApplication
from eidos_runtime.model.discovery import CustomProviderRequest, CustomProviderResult, discover_custom_models
from eidos_runtime.application.projects import ProjectApplication
from eidos_runtime.application.search import SearchApplication
from eidos_runtime.persistence.search import SearchRepository
from eidos_runtime.protocol import search as search_dtos
from eidos_runtime.db.repositories.sessions import SessionRepository
from eidos_runtime.application.sidebar import SidebarApplication
from eidos_runtime.application.queued_runs import QueuedRunApplication
from eidos_runtime.application.automations import AutomationApplication
from eidos_runtime.domain.automation import AutomationError, AutomationResponse
from eidos_runtime.protocol.automations import AutomationExecuteRequest, AutomationHistoryRequest, AutomationRunNowRequest, AutomationRefreshRequest
from eidos_runtime.runtime.automation_scheduler import AutomationScheduler
from eidos_runtime.persistence.queued_runs import QueuedRunRepository
from eidos_runtime.protocol import queued_runs as queued_run_dtos
from eidos_runtime.persistence.sidebar import SidebarRepository
from eidos_runtime.protocol import sidebar as sidebar_dtos
from eidos_runtime.application.runs import RunApplication, RunStartOutcome
from eidos_runtime.application.repository import (
    RepositoryApplicationFactory,
    RepositoryWatcherShutdownError,
    RepositoryWorkspaceRuntime,
)
from eidos_runtime.application.review import ReviewApplication
from eidos_runtime.application.session_lifecycle import SessionLifecycleCoordinator
from eidos_runtime.application.session_fork import SessionForkApplication
from eidos_runtime.persistence.session_history import SessionHistoryRepository
from eidos_runtime.protocol.session_fork import SessionForkRequest, SessionForkResponse

from eidos_runtime.application.sessions import (
    DeferredGitFetch,
    DeferredGitPull,
    DeferredGitPush,
    SessionApplication,
    clean_session_title,
)
from eidos_runtime.application.worktree_retention import WorktreeRetentionService
from eidos_runtime.application.workspace import WorkspaceExplorerApplication
from eidos_runtime.application.task_lifecycle import (
    LifecycleAction,
    LifecycleResult,
    TaskLifecycleApplication,
)
from eidos_runtime.model.client import (
    ModelClient,
    ModelResponse,
    ModelToolCall,
    ScriptedModel,
)
from eidos_runtime.model.config import (
    ModelConfig,
    ModelConfigError,
    ModelConfigStore,
)
from eidos_runtime.model.pydantic_ai_client import (
    ModelClientLease,
)
from eidos_runtime.application.memory import MEMORY_METHODS, MemoryApplication
from eidos_runtime.memory.jobs import MemoryJobs
from eidos_runtime.telemetry.tracing import startup_phase
from eidos_runtime.model.gateway import ModelGateway
from eidos_runtime.domain.long_task import LongTaskProgress
from eidos_runtime.git.manager import WorktreeManager
from eidos_runtime.persistence.review_comments import ReviewCommentRepository
from eidos_runtime.protocol import methods as method_dtos
from eidos_runtime.protocol import review as review_dtos
from eidos_runtime.protocol.registry import (
    DeferredMethodResult,
    MethodApplicationError,
    MethodErrorMapping,
    MethodRegistration,
    MethodRegistry,
    MethodResultValidationError,
    MethodValidationError,
)
from eidos_runtime.protocol.schemas import (
    ApprovalDecisionDto,
    JsonRpcRequestDto,
    JsonRpcResponse,
)
from eidos_runtime.runtime.supervisor import (
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
from eidos_runtime.runtime.runtime_dependencies import (
    RuntimeDependencyCatalog,
    RuntimeDependencyCatalogError,
    discover_runtime_dependency_catalog,
)
from eidos_runtime.sandbox.sensitive import (
    SensitiveContentDenied,
    SensitiveScanError,
    SensitiveScanner,
)
from eidos_runtime.sandbox.seatbelt import (
    run_seatbelt_self_test,
)
from eidos_runtime.db.storage import (
    ResourceNotFoundError,
    SessionStore,
    StorageError,
)
from eidos_runtime.domain.input_reference import InputSnapshot
from eidos_runtime.application.input_context import InputContextApplication
from eidos_runtime.application.agent_catalog import AgentCatalog
from eidos_runtime.application.agent_definitions import AgentDefinitionsApplication
from eidos_runtime.protocol.collaboration import (AgentDefinitionReadRequest, AgentDefinitionSaveRequest, AgentDefinitionRemoveRequest, AgentDefinitionDocument)
from eidos_runtime.domain.agents import AgentCatalogResponse
from eidos_runtime.application.planning import PlanningApplication
from eidos_runtime.protocol import planning as planning_dtos
from eidos_runtime.protocol.collaboration import AgentCatalogRequest, AgentReadRequest, AgentStopRequest
from eidos_runtime.domain.collaboration import CollaborationState
from eidos_runtime.protocol import input_context as input_dtos
from eidos_runtime.extensions.plugins import PluginCatalog
from eidos_runtime.extensions.skill_management import SkillManagement
from eidos_runtime.extensions.skills import (
    SkillCatalog,
    SkillReadError,
    deploy_system_skills,
)


MAX_MESSAGE_BYTES = 1024 * 1024
MAX_REQUEST_ID_BYTES = 128
PROTOCOL_VERSION = 1
CLIENT_REQUEST_ID = re.compile(r"client-[A-Za-z0-9._-]+")
TITLE_TIMEOUT_SECONDS = 10.0

logger = logging.getLogger("eidos.runtime")


def response(request_id: str, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def protocol_error(request_id: str | None, code: int, message: str) -> dict[str, Any]:
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
        if "id" not in message:
            raise ValueError("outbound protocol notification exceeds 1 MiB")
        message = business_error(message["id"], "RESPONSE_TOO_LARGE")
        serialized = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
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


def _application_error_mapping(error: Exception) -> MethodErrorMapping | None:
    """Map only stable application failures at the protocol boundary."""
    if isinstance(error, AutomationError):
        if error.code == "invalid_arguments":
            return MethodErrorMapping("INVALID_PARAMS")
        return MethodErrorMapping(error.code.upper())

    if isinstance(error, ApplicationInvalidParamsError):
        return MethodErrorMapping(error.code, invalid_params=True)
    if isinstance(error, ApplicationError):
        return MethodErrorMapping(error.code)
    return None


def _application_wire_value(value: object) -> object:
    """Project a typed application result without constructing an envelope."""

    to_wire_dict = getattr(value, "to_wire_dict", None)
    if callable(to_wire_dict):
        return to_wire_dict()
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True)
    return value


class _ApplicationMethodAdapter:
    """Turns one typed Application use case into its declared response DTO."""

    def __init__(
        self,
        handler: Any,
        response_type: type[method_dtos.MethodResultDto],
    ) -> None:
        self._handler = handler
        self._response_type = response_type
        self._run_start_outcomes: dict[int, RunStartOutcome] = {}

    def __call__(self, request_id: str, request: BaseModel) -> BaseModel:
        result = self._handler(request_id, request)
        if isinstance(result, RunStartOutcome):
            response = result.response
            self._run_start_outcomes[id(response)] = result
            return response
        if isinstance(result, self._response_type):
            return result
        return self._response_type.model_validate(_application_wire_value(result))

    def settle_response(self, response: BaseModel, delivered: bool) -> None:
        outcome = self._run_start_outcomes.pop(id(response), None)
        if outcome is None:
            return
        if delivered:
            outcome.mark_response_delivered()
        else:
            outcome.mark_response_failed()


class _DeferredPluginImportAdapter:
    """Schedules a typed Plugin import without a compatibility server handler."""

    def __init__(self, server: "RuntimeServer") -> None:
        self._server = server

    def __call__(
        self,
        request_id: str,
        request: method_dtos.PluginImportRequestDto,
    ) -> BaseModel | DeferredMethodResult:
        result = self._server._applications_or_error().extensions.prepare_plugin_import(
            source_path=Path(request.source_path),
            operation_id=request.operation_id,
            request_id=request_id,
        )
        if isinstance(result, PluginImportCompleted):
            return method_dtos.PluginImportResponseDto.model_validate(
                result.plugin.to_wire_dict()
            )
        if not isinstance(result, DeferredPluginImport):
            raise ApplicationError("INTERNAL_ERROR", "unexpected plugin import result")
        scheduled = self._server.supervisor.start_managed_task(
            "plugin-import",
            lambda cancel: self._complete(request_id, result, cancel),
            operation_id=result.async_operation_id,
        )
        if not scheduled:
            failure = result.cancel_before_start()
            raise ApplicationError(failure.code)
        return DeferredMethodResult()

    def _complete(
        self,
        request_id: str,
        deferred: DeferredPluginImport,
        cancel: threading.Event,
    ) -> None:
        result = deferred.run(cancel)
        if isinstance(result, PluginImportCompleted):
            response_dto = method_dtos.PluginImportResponseDto.model_validate(
                result.plugin.to_wire_dict()
            )
            self._server.send(response(request_id, response_dto.to_json_value()))
            return
        if isinstance(result, PluginImportFailure):
            self._server.send(business_error(request_id, result.code))
            return
        self._server.send(business_error(request_id, "INTERNAL_ERROR"))


class _DeferredGitFetchAdapter:
    """Schedules Remote Git without blocking the JSON-RPC input loop."""

    def __init__(self, server: "RuntimeServer") -> None:
        self._server = server

    def __call__(
        self,
        request_id: str,
        request: method_dtos.SessionGitFetchRequestDto,
    ) -> BaseModel | DeferredMethodResult:
        result = self._server._applications_or_error().sessions.prepare_git_fetch(
            request, request_id=request_id
        )
        if isinstance(result, method_dtos.SessionGitFetchResponseDto):
            return result
        if not isinstance(result, DeferredGitFetch):
            raise ApplicationError("INTERNAL_ERROR")
        scheduled = self._server.supervisor.start_managed_task(
            "git-fetch",
            lambda cancel: self._complete(request_id, result, cancel),
            operation_id=result.async_operation_id,
        )
        if not scheduled:
            result.cancel_before_start()
            raise ApplicationError("RUNTIME_DRAINING")
        return DeferredMethodResult()

    def _complete(
        self,
        request_id: str,
        deferred: DeferredGitFetch,
        cancel: threading.Event,
    ) -> None:
        try:
            result = deferred.run(cancel)
        except ApplicationError as error:
            self._server.send(business_error(request_id, error.code))
            return
        except Exception:
            logger.exception("Deferred Git Fetch failed")
            self._server.send(business_error(request_id, "INTERNAL_ERROR"))
            return
        self._server.send(response(request_id, result.to_json_value()))


class _DeferredGitPullAdapter:
    def __init__(self, server: "RuntimeServer") -> None:
        self._server = server

    def __call__(
        self,
        request_id: str,
        request: method_dtos.SessionGitPullRequestDto,
    ) -> BaseModel | DeferredMethodResult:
        result = self._server._applications_or_error().sessions.prepare_git_pull(
            request, request_id=request_id
        )
        if isinstance(result, method_dtos.SessionGitPullResponseDto):
            return result
        if not isinstance(result, DeferredGitPull):
            raise ApplicationError("INTERNAL_ERROR")
        scheduled = self._server.supervisor.start_managed_task(
            "git-pull",
            lambda cancel: self._complete(request_id, result, cancel),
            operation_id=result.async_operation_id,
        )
        if not scheduled:
            result.cancel_before_start()
            raise ApplicationError("RUNTIME_DRAINING")
        return DeferredMethodResult()

    def _complete(
        self,
        request_id: str,
        deferred: DeferredGitPull,
        cancel: threading.Event,
    ) -> None:
        try:
            result = deferred.run(cancel)
        except ApplicationError as error:
            self._server.send(business_error(request_id, error.code))
            return
        except Exception:
            logger.exception("Deferred Git Pull failed")
            self._server.send(business_error(request_id, "INTERNAL_ERROR"))
            return
        self._server.send(response(request_id, result.to_json_value()))


class _DeferredGitPushAdapter:
    def __init__(self, server: "RuntimeServer") -> None:
        self._server = server

    def __call__(
        self,
        request_id: str,
        request: method_dtos.SessionGitPushRequestDto,
    ) -> BaseModel | DeferredMethodResult:
        result = self._server._applications_or_error().sessions.prepare_git_push(
            request, request_id=request_id
        )
        if isinstance(result, method_dtos.SessionGitPushResponseDto):
            return result
        if not isinstance(result, DeferredGitPush):
            raise ApplicationError("INTERNAL_ERROR")
        scheduled = self._server.supervisor.start_managed_task(
            "git-push",
            lambda cancel: self._complete(request_id, result, cancel),
            operation_id=result.async_operation_id,
        )
        if not scheduled:
            result.cancel_before_start()
            raise ApplicationError("RUNTIME_DRAINING")
        return DeferredMethodResult()

    def _complete(
        self,
        request_id: str,
        deferred: DeferredGitPush,
        cancel: threading.Event,
    ) -> None:
        try:
            result = deferred.run(cancel)
        except ApplicationError as error:
            self._server.send(business_error(request_id, error.code))
            return
        except Exception:
            logger.exception("Deferred Git Push failed")
            self._server.send(business_error(request_id, "INTERNAL_ERROR"))
            return
        self._server.send(response(request_id, result.to_json_value()))


class _CustomProviderAdapter:
    def __init__(self, server: "RuntimeServer") -> None:
        self._server = server

    def __call__(self, request_id: str, request: CustomProviderRequest) -> DeferredMethodResult:
        base_url, api_key = self._server._applications_or_error().models.discovery_credentials(request)

        def run(cancel: threading.Event) -> None:
            try:
                result = self._server._required_async_kernel().call(discover_custom_models, base_url, api_key, cancel)
                payload = CustomProviderResult.model_validate(result).to_wire_dict(exclude_none=True)
                self._server.send(response(request_id, payload))
            except Exception:
                # Never log a request or third-party exception containing a credential.
                self._server.send(business_error(request_id, "INTERNAL_ERROR"))

        if not self._server.supervisor.start_managed_task("custom-provider-models", run):
            raise ApplicationError("RUNTIME_DRAINING")
        return DeferredMethodResult()


class _DeferredGitObserveAdapter:
    """Offload read-only Git observes from the JSON-RPC input loop.

    Slow `git status/diff/context` must not head-of-line block cheap reads
    such as `session/read` or `model/presets`. The input loop only validates
    the request and schedules a supervisor managed task; the background task
    sends the response later via the thread-safe `RuntimeServer.send`.
    """

    def __init__(
        self,
        server: "RuntimeServer",
        kind: str,
        method_name: str,
    ) -> None:
        self._server = server
        self._kind = kind
        self._method_name = method_name

    def __call__(
        self, request_id: str, request: BaseModel
    ) -> BaseModel | DeferredMethodResult:
        method = getattr(
            self._server._applications_or_error().sessions, self._method_name
        )

        def _run(_cancel: threading.Event) -> None:
            try:
                result = method(request)
            except ApplicationError as error:
                self._server.send(business_error(request_id, error.code))
                return
            except Exception:
                logger.exception("Deferred Git observe failed: %s", self._kind)
                self._server.send(business_error(request_id, "INTERNAL_ERROR"))
                return
            try:
                to_wire = getattr(result, "to_json_value", None)
                payload = (
                    to_wire()
                    if callable(to_wire)
                    else result.model_dump(mode="json", by_alias=True)
                )
            except Exception:
                logger.exception(
                    "Deferred Git observe serialization failed: %s", self._kind
                )
                self._server.send(business_error(request_id, "INTERNAL_ERROR"))
                return
            self._server.send(response(request_id, payload))

        scheduled = self._server.supervisor.start_managed_task(self._kind, _run)
        if not scheduled:
            # Draining/reconfiguring: preserve current read availability by
            # falling back to synchronous execution on the input loop.
            return method(request)
        return DeferredMethodResult()


class _DeferredSessionForkAdapter:
    """Keep history capture and Git IO off the protocol input loop."""

    def __init__(self, server: "RuntimeServer") -> None:
        self._server = server

    def __call__(self, request_id: str, request: SessionForkRequest) -> DeferredMethodResult:
        application = self._server._applications_or_error().session_forks

        def complete(cancel: threading.Event) -> None:
            try:
                result = application.fork(request, cancel)
            except ApplicationError as error:
                self._server.send(business_error(request_id, error.code))
                return
            except Exception:
                logger.exception("Conversation fork failed")
                self._server.send(business_error(request_id, "INTERNAL_ERROR"))
                return
            self._server.send(response(request_id, result.to_json_value()))

        if not self._server.supervisor.start_managed_task("session-fork", complete):
            raise ApplicationError("RUNTIME_DRAINING", "runtime is draining")
        return DeferredMethodResult()


@dataclass(frozen=True)
class _RuntimeApplications:
    search: SearchApplication
    queued_runs: QueuedRunApplication
    sidebar: SidebarApplication
    projects: ProjectApplication
    sessions: SessionApplication
    session_forks: SessionForkApplication
    runs: RunApplication
    approvals: ApprovalApplication
    models: ModelApplication
    extensions: ExtensionApplication
    repository_factory: RepositoryApplicationFactory
    repository_runtime: RepositoryWorkspaceRuntime
    context: ContextApplication
    checkpoints: CheckpointApplication
    task_lifecycle: TaskLifecycleApplication
    workspace: WorkspaceExplorerApplication
    review: ReviewApplication


class _ServerRunEnvironment:
    """Narrow non-durable Run collaborators owned by RuntimeServer."""

    def __init__(self, server: "RuntimeServer") -> None:
        self._server = server

    def validate_input_selections(self, snapshots: list[InputSnapshot]) -> None:
        try:
            self._server._input_application().validate_selections(snapshots)
        except ValueError as error:
            raise ApplicationError("INVALID_STATE", "引用扩展不可用，请重新选择。") from error

    def model_is_configured(self) -> bool:
        return (
            self._server.model is not None
            or bool(self._server.model_config.list())
        )

    def model_config(self, model_id: str) -> ModelConfig:
        config = self._server.model_config.get(model_id)
        if config is None:
            raise ModelConfigError("model is not configured")
        return config

    def model_for(self, model_id: str) -> ModelClient:
        if self._server.model is None:
            raise ModelConfigError("configured models use the model gateway")
        return self._server.model

    def freeze_model_config(self, run_id: str, config: ModelConfig) -> None:
        self._server._freeze_model_config(run_id, config)

    def discard_model_config(self, run_id: str) -> None:
        self._server._discard_model_config(run_id)

    def extension_snapshot(self) -> dict[str, object] | None:
        if self._server.plugins is None:
            return None
        return SkillCatalog(self._server.plugins).extension_snapshot()

    def schedule_title_generation(
        self, session_id: str, user_input: str, model_id: str
    ) -> None:
        self._server._schedule_title_generation(session_id, user_input, model_id)


class _ServerApprovalRuntime:
    def __init__(self, supervisor: RunSupervisor) -> None:
        self._supervisor = supervisor

    def submit_approval_response(
        self,
        *,
        request_id: str,
        decision: ApplicationApprovalDecision,
        feedback: str | None,
    ) -> bool:
        return self._supervisor.submit_approval_response(
            request_id=request_id,
            decision=decision.value,
            feedback=feedback,
        )


class _ServerTaskLifecycleRuntime:
    def __init__(self, supervisor: RunSupervisor) -> None:
        self._supervisor = supervisor

    def pause_run(self, run_id: str) -> LifecycleResult:
        progress = self._supervisor.pause_run(run_id)
        return LifecycleResult(
            action=LifecycleAction.PAUSE,
            accepted=progress.status.value == "paused",
            reason=None if progress.status.value == "paused" else progress.status.value,
        )

    def resume_run(self, run_id: str) -> LifecycleResult:
        progress = self._supervisor.resume_run(run_id)
        return LifecycleResult(
            action=LifecycleAction.RESUME,
            accepted=progress.status.value == "running",
            reason=(
                None
                if progress.status.value == "running"
                else progress.last_verification.outcome.value
                if progress.last_verification is not None
                else progress.status.value
            ),
        )

    def run_status(self, run_id: str) -> LongTaskProgress | None:
        return self._supervisor.run_status(run_id)

    def cancel_run(
        self, run_id: str, *, operation_id: str | None = None
    ) -> LifecycleResult:
        self._supervisor.cancel_run(run_id, operation_id=operation_id)
        return LifecycleResult(action=LifecycleAction.CANCEL, accepted=True)


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
        self.model_gateway: ModelGateway | None = None
        self.memory_jobs: MemoryJobs | None = None
        self.channel_service: ChannelService | None = None
        self.automation_scheduler: AutomationScheduler | None = None
        self._frozen_model_configs: dict[str, ModelConfig] = {}
        self._frozen_model_configs_lock = threading.RLock()
        self.async_kernel: RuntimeAsyncKernel | None = None
        self.runtime_dependency_catalog: RuntimeDependencyCatalog | None = None
        self.output_lock = threading.RLock()
        self._storage_log_handler: logging.Handler | None = None
        self.shell_available = False
        self.sensitive: SensitiveScanner | None = None
        self.plugins: PluginCatalog | None = None
        self.worktree_manager: WorktreeManager | None = None
        self.worktree_retention: WorktreeRetentionService | None = None
        self._applications: _RuntimeApplications | None = None
        self.repository_factory = RepositoryApplicationFactory(
            self.store.repository_intelligence_repository
        )
        self.repository_runtime = RepositoryWorkspaceRuntime(
            self.repository_factory
        )
        self.supervisor = RunSupervisor(
            self.store,
            self._model_lease_for_run,
            self.send,
            self._scan_text,
            lambda: self.model is not None or bool(self.model_config.list()),
            lambda: self.shell_available,
            lambda: self.sensitive,
            self._cleanup_extensions,
            repository_runtime=self.repository_runtime,
        )
        self.method_registry = self._build_method_registry()

    def handle(self, message: object) -> None:
        if not isinstance(message, dict):
            self.send(protocol_error(None, -32600, "Invalid Request"))
            return

        if self._is_server_response(message):
            request_id = message.get("id")
            if not isinstance(request_id, str):
                return
            try:
                decision = ApprovalDecisionDto.model_validate(message.get("result"))
            except ValidationError:
                decision = ApprovalDecisionDto(decision="reject")
            applications = self._applications_or_error()
            if decision.decision == "approve":
                applications.approvals.approve(request_id)
            else:
                applications.approvals.reject(request_id, feedback=decision.feedback)
            return

        try:
            request = JsonRpcRequestDto.model_validate(
                {**message, "params": message.get("params", {})}
            )
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
            invocation = self.method_registry.invoke(method, request_id, params)
        except MethodValidationError:
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        except MethodApplicationError as error:
            if error.mapping.invalid_params:
                self.send(protocol_error(request_id, -32602, "Invalid params"))
            else:
                self.send(business_error(request_id, error.mapping.code))
            return
        except MethodResultValidationError:
            logger.error("Registered method returned an invalid response: %s", method)
            self.send(business_error(request_id, "INTERNAL_ERROR"))
            return
        if invocation is not False:
            if invocation.deferred:
                return
            assert invocation.json_result is not None
            try:
                self.send(response(request_id, invocation.json_result))
            except BaseException:
                invocation.settle_response(delivered=False)
                raise
            invocation.settle_response(delivered=True)
            return
        self.send(protocol_error(request_id, -32601, "Method not found"))

    def _build_method_registry(self) -> MethodRegistry:
        registry = MethodRegistry()
        handlers: tuple[
            tuple[str, type[BaseModel], type[method_dtos.MethodResultDto], Any], ...
        ] = (
            ("channel/read", ChannelReadRequest, ChannelResponse, lambda _id, request: self._channel_read(request)),
            ("channel/save", ChannelSaveRequest, ChannelResponse, lambda _id, request: self._channel_save(request)),
            (
                "schedule/execute", AutomationExecuteRequest, AutomationResponse,
                lambda _id, request: self._automation_application().execute(request.command,
                    context_session_id=request.context_session_id, operation_id=request.operation_id,
                    full_access_confirmation=request.full_access_confirmation),
            ),
            (
                "schedule/history", AutomationHistoryRequest, AutomationResponse,
                lambda _id, request: self._automation_history(request),
            ),
            (
                "schedule/runNow", AutomationRunNowRequest, AutomationResponse,
                lambda _id, request: self._automation_application().run_now(request.id, request.operation_id or _id),
            ),
            (
                "schedule/refresh", AutomationRefreshRequest, AutomationResponse,
                lambda _id, _request: self._refresh_automation_schedule(),
            ),
            (
                "run/listQueued",
                queued_run_dtos.QueuedRunListRequest,
                queued_run_dtos.QueuedRunListResponse,
                lambda _id, request: self._applications_or_error().queued_runs.list(request),
            ),
            (
                "run/updateQueued",
                queued_run_dtos.QueuedRunUpdateRequest,
                queued_run_dtos.QueuedRunChangeResponse,
                lambda _id, request: self._applications_or_error().queued_runs.update(request),
            ),
            (
                "run/steerQueued",
                queued_run_dtos.QueuedRunSteerRequest,
                queued_run_dtos.QueuedRunChangeResponse,
                lambda _id, request: self._applications_or_error().queued_runs.steer(request),
            ),
            (
                "run/deleteQueued",
                queued_run_dtos.QueuedRunDeleteRequest,
                queued_run_dtos.QueuedRunChangeResponse,
                lambda _id, request: self._applications_or_error().queued_runs.delete(request),
            ),
            (
                "sidebar/listPins",
                sidebar_dtos.SidebarListPinsRequest,
                sidebar_dtos.SidebarPinsResponse,
                lambda _id, request: self._applications_or_error().sidebar.list_pins(request),
            ),
            (
                "sidebar/setPinned",
                sidebar_dtos.SidebarSetPinnedRequest,
                sidebar_dtos.SidebarPinsResponse,
                lambda _id, request: self._applications_or_error().sidebar.set_pinned(request),
            ),
            (
                "project/list",
                method_dtos.ProjectListRequestDto,
                method_dtos.ProjectListResponseDto,
                lambda _id, request: self._applications_or_error().projects.list(
                    request
                ),
            ),
            (
                "project/create",
                method_dtos.ProjectCreateRequestDto,
                method_dtos.ProjectCreateResponseDto,
                lambda _id, request: self._applications_or_error().projects.create(
                    request
                ),
            ),
            (
                "project/delete",
                method_dtos.ProjectDeleteRequestDto,
                method_dtos.ProjectDeleteResponseDto,
                lambda _id, request: self._applications_or_error().projects.delete(
                    request
                ),
            ),
            (
                "session/create",
                method_dtos.SessionCreateRequestDto,
                method_dtos.SessionCreateResponseDto,
                lambda _id, request: self._applications_or_error().sessions.create(
                    request
                ),
            ),
            (
                "session/fork", SessionForkRequest, SessionForkResponse,
                lambda _id, request: self._applications_or_error().session_forks.fork(request),
            ),
            (
                "session/createBranch",
                method_dtos.SessionCreateBranchRequestDto,
                method_dtos.SessionCreateBranchResponseDto,
                lambda _id, request: self._applications_or_error().sessions.create_branch(
                    request
                ),
            ),
            (
                "session/gitSwitchBranch",
                method_dtos.SessionGitSwitchBranchRequestDto,
                method_dtos.SessionGitSwitchBranchResponseDto,
                lambda _id, request: self._applications_or_error().sessions.git_switch_branch(
                    request
                ),
            ),
            (
                "session/gitCreateBranch",
                method_dtos.SessionGitCreateBranchRequestDto,
                method_dtos.SessionGitCreateBranchResponseDto,
                lambda _id, request: self._applications_or_error().sessions.git_create_branch(
                    request
                ),
            ),
            (
                "session/list",
                method_dtos.SessionListRequestDto,
                method_dtos.SessionListResponseDto,
                lambda _id, request: self._applications_or_error().sessions.list(
                    request
                ),
            ),
            (
                "toolCall/readText",
                method_dtos.ToolTextReadRequestDto,
                method_dtos.ToolTextReadResponseDto,
                lambda _id, request: self._applications_or_error().sessions.read_tool_text(request),
            ),
            (
                "search/sessions", search_dtos.SearchRequest, search_dtos.SearchResponse,
                lambda _id, request: self._applications_or_error().search.search(request),
            ),
            (
                "session/readAround", search_dtos.ReadAroundRequest, method_dtos.SessionReadResponseDto,
                lambda _id, request: self._applications_or_error().search.read_around(request),
            ),
            (
                "session/read",
                method_dtos.SessionReadRequestDto,
                method_dtos.SessionReadResponseDto,
                lambda _id,
                request: self._applications_or_error().sessions.read_snapshot(request),
            ),
            (
                "workspace/listDirectory",
                method_dtos.WorkspaceListDirectoryRequestDto,
                method_dtos.WorkspaceListDirectoryResponseDto,
                lambda _id, request: self._applications_or_error().workspace.list_directory(
                    request
                ),
            ),
            (
                "workspace/readAsset",
                method_dtos.WorkspaceReadAssetRequestDto,
                method_dtos.WorkspaceReadAssetResponseDto,
                lambda _id, request: self._applications_or_error().workspace.read_asset(request),
            ),
            (
                "workspace/readFileForFind", search_dtos.FileFindRequest, search_dtos.FileFindResponse,
                lambda _id, request: self._applications_or_error().workspace.read_file_for_find(request),
            ),
            (
                "workspace/readFilePreview",
                method_dtos.WorkspaceReadFilePreviewRequestDto,
                method_dtos.WorkspaceReadFilePreviewResponseDto,
                lambda _id, request: self._applications_or_error().workspace.read_file_preview(
                    request
                ),
            ),
            (
                "review/listComments",
                review_dtos.ReviewCommentListRequestDto,
                review_dtos.ReviewCommentListResponseDto,
                lambda _id, request: self._applications_or_error().review.list_comments(
                    request
                ),
            ),
            (
                "review/createComment",
                review_dtos.ReviewCommentCreateRequestDto,
                review_dtos.ReviewCommentCreateResponseDto,
                lambda _id, request: self._applications_or_error().review.create_comment(
                    request
                ),
            ),
            (
                "review/deleteComment",
                review_dtos.ReviewCommentDeleteRequestDto,
                review_dtos.ReviewCommentDeleteResponseDto,
                lambda _id, request: self._applications_or_error().review.delete_comment(
                    request
                ),
            ),
            (
                "session/gitStage",
                method_dtos.SessionGitStageRequestDto,
                method_dtos.SessionGitStageResponseDto,
                lambda _id, request: self._applications_or_error().sessions.git_stage(
                    request
                ),
            ),
            (
                "session/gitCommit",
                method_dtos.SessionGitCommitRequestDto,
                method_dtos.SessionGitCommitResponseDto,
                lambda _id, request: self._applications_or_error().sessions.git_commit(
                    request
                ),
            ),
            (
                "session/gitMerge",
                method_dtos.SessionGitMergeRequestDto,
                method_dtos.SessionGitMergeResponseDto,
                lambda _id, request: self._applications_or_error().sessions.git_merge(
                    request
                ),
            ),
            (
                "session/gitMergeAbort",
                method_dtos.SessionGitMergeAbortRequestDto,
                method_dtos.SessionGitMergeAbortResponseDto,
                lambda _id, request: self._applications_or_error().sessions.git_merge_abort(
                    request
                ),
            ),
            (
                "session/gitRebase",
                method_dtos.SessionGitRebaseRequestDto,
                method_dtos.SessionGitRebaseResponseDto,
                lambda _id, request: self._applications_or_error().sessions.git_rebase(
                    request
                ),
            ),
            (
                "session/gitRebaseContinue",
                method_dtos.SessionGitRebaseContinueRequestDto,
                method_dtos.SessionGitRebaseContinueResponseDto,
                lambda _id, request: self._applications_or_error().sessions.git_rebase_continue(
                    request
                ),
            ),
            (
                "session/gitRebaseAbort",
                method_dtos.SessionGitRebaseAbortRequestDto,
                method_dtos.SessionGitRebaseAbortResponseDto,
                lambda _id, request: self._applications_or_error().sessions.git_rebase_abort(
                    request
                ),
            ),
            (
                "session/gitRemoteStatus",
                method_dtos.SessionGitRemoteStatusRequestDto,
                method_dtos.SessionGitRemoteStatusResponseDto,
                lambda _id,
                request: self._applications_or_error().sessions.git_remote_status(
                    request
                ),
            ),
            (
                "session/rename",
                method_dtos.SessionRenameRequestDto,
                method_dtos.SessionRenameResponseDto,
                lambda _id, request: self._applications_or_error().sessions.rename(
                    request
                ),
            ),
            (
                "session/delete",
                method_dtos.SessionDeleteRequestDto,
                method_dtos.SessionDeleteResponseDto,
                lambda _id, request: self._applications_or_error().sessions.delete(
                    request
                ),
            ),
            (
                "session/handoff",
                method_dtos.SessionHandoffRequestDto,
                method_dtos.SessionHandoffResponseDto,
                lambda _id, request: self._applications_or_error().sessions.handoff(
                    request
                ),
            ),
            (
                "session/restoreWorktree",
                method_dtos.SessionRestoreWorktreeRequestDto,
                method_dtos.SessionRestoreWorktreeResponseDto,
                lambda _id, request: self._applications_or_error().sessions.restore_worktree(
                    request
                ),
            ),
            (
                "settings/read",
                method_dtos.SettingsReadRequestDto,
                method_dtos.SettingsReadResponseDto,
                lambda _id, _request: self._worktree_settings_response(),
            ),
            (
                "settings/update",
                method_dtos.SettingsUpdateRequestDto,
                method_dtos.SettingsUpdateResponseDto,
                lambda _id, request: self._worktree_settings_update(request),
            ),
            (
                "event/list",
                method_dtos.EventListRequestDto,
                method_dtos.EventListResponseDto,
                lambda _id, request: self._applications_or_error().sessions.list_events(
                    request
                ),
            ),
            *((method, request, response,
               lambda _id, payload, method=method: MemoryApplication(self.store, self.memory_jobs).dispatch(method, payload))
              for method, request, response in MEMORY_METHODS),
            ("agent/catalog", AgentCatalogRequest, AgentCatalogResponse,
             lambda _id, _request: AgentCatalog(self.store.database.data_directory, self.model_config).list()),
            ("agent/definitionRead", AgentDefinitionReadRequest, AgentDefinitionDocument,
             lambda _id, request: self._agent_definitions().read(request)),
            ("agent/definitionSave", AgentDefinitionSaveRequest, AgentDefinitionDocument,
             lambda _id, request: self._agent_definitions().save(request)),
            ("agent/definitionRemove", AgentDefinitionRemoveRequest, AgentCatalogResponse,
             lambda _id, request: self._agent_definitions().remove(request)),
            ("agent/read", AgentReadRequest, CollaborationState,
             lambda _id, request: self.supervisor.collaboration.read_session(request.session_id)),
            ("agent/stop", AgentStopRequest, CollaborationState,
             lambda _id, request: self.supervisor.collaboration.stop_from_desktop(request.parent_run_id, request.agent_id)),
            ("planning/read", planning_dtos.PlanningReadRequest, planning_dtos.PlanningReadResponse,
             lambda _id, request: self._planning_application().read(request)),
            ("planning/answer", planning_dtos.AnswerInputRequest, planning_dtos.AnswerInputResponse,
             lambda _id, request: self._planning_application().answer(request)),
            ("plan/read", planning_dtos.PlanReadRequest, planning_dtos.PlanResponse,
             lambda _id, request: self._planning_application().read_plan(request)),
            ("plan/edit", planning_dtos.PlanEditRequest, planning_dtos.PlanResponse,
             lambda _id, request: self._planning_application().edit(request)),
            ("input/prepare", input_dtos.InputPrepareRequest, input_dtos.InputReferenceResponse,
             lambda _id, request: self._input_application().prepare(request)),
            ("input/read", input_dtos.InputReadRequest, input_dtos.InputPreviewResponse,
             lambda _id, request: self._input_application().read(request)),
            ("input/readAsset", input_dtos.InputReadAssetRequest, input_dtos.InputReadAssetResponse,
             lambda _id, request: self._input_application().read_asset(request)),
            ("input/draftRead", input_dtos.DraftReadRequest, input_dtos.DraftResponse,
             lambda _id, request: self._input_application().read_draft(request)),
            ("input/draftWrite", input_dtos.DraftWriteRequest, input_dtos.DraftResponse,
             lambda _id, request: self._input_application().write_draft(request)),
            (
                "run/start",
                method_dtos.RunStartRequestDto,
                method_dtos.RunStartResponseDto,
                lambda _id, request: self._applications_or_error().runs.start(request),
            ),
            (
                "run/cancel",
                method_dtos.RunCancelRequestDto,
                method_dtos.RunCancelResponseDto,
                lambda _id, request: self._applications_or_error().runs.cancel(request),
            ),
            (
                "run/status",
                method_dtos.RunStatusRequestDto,
                method_dtos.RunStatusResponseDto,
                lambda _id, request: self._applications_or_error().runs.status(request),
            ),
            (
                "context/usage",
                method_dtos.ContextUsageRequestDto,
                method_dtos.ContextUsageResponseDto,
                lambda _id, request: self._applications_or_error().runs.context_usage(
                    request
                ),
            ),
            ("context/details", method_dtos.ContextDetailsRequestDto, ContextDetails,
             lambda _id, request: self._applications_or_error().context.details(request.run_id)),
            ("context/compact", method_dtos.ContextCompactRequestDto, ContextCompactionRecord,
             lambda _id, request: self._applications_or_error().context.request_compaction(request.run_id)),
            (
                "run/pause",
                method_dtos.RunPauseRequestDto,
                method_dtos.RunPauseResponseDto,
                lambda _id, request: self._applications_or_error().runs.pause(request),
            ),
            (
                "run/resume",
                method_dtos.RunResumeRequestDto,
                method_dtos.RunResumeResponseDto,
                lambda _id, request: self._applications_or_error().runs.resume(request),
            ),
            (
                "checkpoint/create",
                method_dtos.CheckpointCreateRequestDto,
                method_dtos.CheckpointCreateResponseDto,
                lambda _id, request: self._applications_or_error().checkpoints.create(request),
            ),
            (
                "checkpoint/list",
                method_dtos.CheckpointListRequestDto,
                method_dtos.CheckpointListResponseDto,
                lambda _id, request: self._applications_or_error().checkpoints.list(request),
            ),
            (
                "checkpoint/rewind",
                method_dtos.CheckpointRewindRequestDto,
                method_dtos.CheckpointRewindResponseDto,
                lambda _id, request: self._applications_or_error().checkpoints.rewind(request),
            ),
            (
                "checkpoint/fork",
                method_dtos.CheckpointForkRequestDto,
                method_dtos.CheckpointForkResponseDto,
                lambda _id, request: self._applications_or_error().checkpoints.fork(request),
            ),
            (
                "model/presets",
                method_dtos.ModelPresetsRequestDto,
                method_dtos.ModelPresetsResponseDto,
                lambda _id, _request: self._applications_or_error().models.presets(),
            ),
            (
                "model/list",
                method_dtos.ModelListRequestDto,
                method_dtos.ModelListResponseDto,
                lambda _id,
                _request: self._applications_or_error().models.list_models(),
            ),
            (
                "model/create",
                method_dtos.ModelCreateRequestDto,
                method_dtos.ModelCreateResponseDto,
                lambda _id, request: self._applications_or_error().models.create(
                    provider=request.provider,
                    model_id=request.model_id,
                    api_key=request.api_key,
                    custom=request.custom,
                ),
            ),
            (
                "model/update",
                method_dtos.ModelUpdateRequestDto,
                method_dtos.ModelUpdateResponseDto,
                lambda _id, request: self._applications_or_error().models.update(
                    request.id,
                    provider=request.provider,
                    model_id=request.model_id,
                    api_key=request.api_key,
                    custom=request.custom,
                ),
            ),
            (
                "model/delete",
                method_dtos.ModelDeleteRequestDto,
                method_dtos.ModelDeleteResponseDto,
                lambda _id, request: self._applications_or_error().models.delete(
                    request.id
                ),
            ),
            (
                "plugin/list",
                method_dtos.PluginListRequestDto,
                method_dtos.PluginListResponseDto,
                lambda _id,
                _request: self._applications_or_error().extensions.list_plugins(),
            ),
            (
                "plugin/setEnabled",
                method_dtos.PluginSetEnabledRequestDto,
                method_dtos.PluginSetEnabledResponseDto,
                lambda _id,
                request: self._applications_or_error().extensions.set_plugin_enabled(
                    plugin_id=request.plugin_id,
                    enabled=request.enabled,
                    operation_id=request.operation_id,
                ),
            ),
            (
                "plugin/remove",
                method_dtos.PluginRemoveRequestDto,
                method_dtos.PluginRemoveResponseDto,
                lambda _id,
                request: self._applications_or_error().extensions.remove_plugin(
                    plugin_id=request.plugin_id, operation_id=request.operation_id
                ),
            ),
            (
                "skill/list",
                method_dtos.SkillListRequestDto,
                method_dtos.SkillListResponseDto,
                lambda _id,
                _request: self._applications_or_error().extensions.list_skills(),
            ),
            (
                "skill/detail",
                method_dtos.SkillDetailRequestDto,
                method_dtos.SkillDetailResponseDto,
                lambda _id, request: self._applications_or_error().extensions.skill_detail(
                    qualified_id=request.qualified_id
                ),
            ),
            (
                "skill/setEnabled",
                method_dtos.SkillSetEnabledRequestDto,
                method_dtos.ManagedSkillDto,
                lambda _id, request: self._applications_or_error().extensions.set_skill_enabled(
                    qualified_id=request.qualified_id, enabled=request.enabled
                ),
            ),
            (
                "skill/remove",
                method_dtos.SkillRemoveRequestDto,
                method_dtos.SkillRemoveResponseDto,
                lambda _id, request: self._applications_or_error().extensions.remove_skill(
                    qualified_id=request.qualified_id
                ),
            ),
            (
                "skill/read",
                method_dtos.SkillReadRequestDto,
                method_dtos.SkillReadResponseDto,
                lambda _id,
                request: self._applications_or_error().extensions.read_skill(
                    qualified_id=request.qualified_id
                ),
            ),
            (
                "mcp/list",
                method_dtos.McpListRequestDto,
                method_dtos.McpListResponseDto,
                lambda _id,
                _request: self._applications_or_error().extensions.list_mcp_servers(),
            ),
            (
                "mcp/setEnabled",
                method_dtos.McpSetEnabledRequestDto,
                method_dtos.McpSetEnabledResponseDto,
                lambda _id,
                request: self._applications_or_error().extensions.set_mcp_enabled(
                    plugin_id=request.plugin_id,
                    server_id=request.server_id,
                    enabled=request.enabled,
                    consent=request.consent,
                    operation_id=request.operation_id,
                ),
            ),
            (
                "mcp/create",
                method_dtos.McpCreateRequestDto,
                method_dtos.McpCreateResponseDto,
                lambda _id,
                request: self._applications_or_error().extensions.create_mcp_server(
                    server_id=request.server_id,
                    executable=request.executable,
                    argv=request.argv,
                    env=request.env,
                    env_names=request.env_names,
                    cwd=request.cwd,
                    permission_profile=request.permission_profile,
                    startup_timeout_seconds=request.startup_timeout_seconds,
                    tool_timeout_seconds=request.tool_timeout_seconds,
                    operation_id=request.operation_id,
                ),
            ),
            (
                "mcp/update",
                method_dtos.McpUpdateRequestDto,
                method_dtos.McpUpdateResponseDto,
                lambda _id,
                request: self._applications_or_error().extensions.update_mcp_server(
                    server_id=request.server_id,
                    executable=request.executable,
                    argv=request.argv,
                    env=request.env,
                    env_names=request.env_names,
                    cwd=request.cwd,
                    permission_profile=request.permission_profile,
                    startup_timeout_seconds=request.startup_timeout_seconds,
                    tool_timeout_seconds=request.tool_timeout_seconds,
                    operation_id=request.operation_id,
                ),
            ),
            (
                "mcp/remove",
                method_dtos.McpRemoveRequestDto,
                method_dtos.McpRemoveResponseDto,
                lambda _id,
                request: self._applications_or_error().extensions.remove_mcp_server(
                    server_id=request.server_id,
                    operation_id=request.operation_id,
                ),
            ),
            (
                "extension/read",
                method_dtos.ExtensionReadRequestDto,
                method_dtos.ExtensionReadResponseDto,
                lambda _id,
                _request: self._applications_or_error().extensions.read_extensions(),
            ),
            (
                "extension/readEvents",
                method_dtos.ExtensionReadEventsRequestDto,
                method_dtos.ExtensionReadEventsResponseDto,
                lambda _id,
                request: self._applications_or_error().extensions.read_extension_events(
                    after_event_id=request.after_event_id, limit=request.limit
                ),
            ),
        )
        draining_blocked = {
            "channel/save",
            "schedule/execute", "schedule/runNow",
            "run/updateQueued",
            "run/deleteQueued",
            "sidebar/setPinned",
            "run/start",
            "input/prepare",
            "input/draftWrite",
            "planning/answer",
            "agent/stop",
            "agent/definitionSave",
            "agent/definitionRemove",
            "plan/edit",
            "model/create",
            "model/discoverCustom",
            "model/update",
            "model/delete",
            "plugin/import",
            "plugin/setEnabled",
            "plugin/remove",
            "mcp/setEnabled",
            "mcp/create",
            "mcp/update",
            "mcp/remove",
        }
        draining_blocked.update(method for method, _request, _response in MEMORY_METHODS if method not in {"memory/list", "memory/get", "memory/export"})
        reconfiguration_blocked = {"channel/save", "run/start", "schedule/execute", "schedule/runNow"}
        for name, request_type, response_type, handler in handlers:
            registry.register(
                MethodRegistration(
                    name=name,
                    request_type=request_type,
                    response_type=response_type,
                    handler=_DeferredSessionForkAdapter(self) if name == "session/fork" else _ApplicationMethodAdapter(handler, response_type),
                    allowed_when_draining=name not in draining_blocked and name != "session/fork",
                    allowed_during_reconfiguration=name not in reconfiguration_blocked and name != "session/fork",
                    error_mapper=_application_error_mapping,
                )
            )
        registry.register(MethodRegistration(
            name="model/discoverCustom", request_type=CustomProviderRequest,
            response_type=CustomProviderResult, handler=_CustomProviderAdapter(self),
            allowed_when_draining=False, allowed_during_reconfiguration=False,
            error_mapper=_application_error_mapping,
        ))
        registry.register(
            MethodRegistration(
                name="session/gitFetch",
                request_type=method_dtos.SessionGitFetchRequestDto,
                response_type=method_dtos.SessionGitFetchResponseDto,
                handler=_DeferredGitFetchAdapter(self),
                allowed_when_draining=False,
                allowed_during_reconfiguration=False,
                error_mapper=_application_error_mapping,
            )
        )
        registry.register(
            MethodRegistration(
                name="session/gitPull",
                request_type=method_dtos.SessionGitPullRequestDto,
                response_type=method_dtos.SessionGitPullResponseDto,
                handler=_DeferredGitPullAdapter(self),
                allowed_when_draining=False,
                allowed_during_reconfiguration=False,
                error_mapper=_application_error_mapping,
            )
        )
        registry.register(
            MethodRegistration(
                name="session/gitPush",
                request_type=method_dtos.SessionGitPushRequestDto,
                response_type=method_dtos.SessionGitPushResponseDto,
                handler=_DeferredGitPushAdapter(self),
                allowed_when_draining=False,
                allowed_during_reconfiguration=False,
                error_mapper=_application_error_mapping,
            )
        )
        registry.register(
            MethodRegistration(
                name="plugin/import",
                request_type=method_dtos.PluginImportRequestDto,
                response_type=method_dtos.PluginImportResponseDto,
                handler=_DeferredPluginImportAdapter(self),
                allowed_when_draining=False,
                allowed_during_reconfiguration=True,
                error_mapper=_application_error_mapping,
            )
        )
        for name, request_type, response_type, method_name in (
            (
                "project/gitContext",
                method_dtos.GitContextRequestDto,
                method_dtos.GitContextResponseDto,
                "git_context",
            ),
            (
                "session/gitStatus",
                method_dtos.SessionGitStatusRequestDto,
                method_dtos.SessionGitStatusResponseDto,
                "git_status",
            ),
            (
                "session/gitDiff",
                method_dtos.SessionGitDiffRequestDto,
                method_dtos.SessionGitDiffResponseDto,
                "git_diff",
            ),
        ):
            registry.register(
                MethodRegistration(
                    name=name,
                    request_type=request_type,
                    response_type=response_type,
                    handler=_DeferredGitObserveAdapter(
                        self, kind=name, method_name=method_name
                    ),
                    allowed_when_draining=True,
                    allowed_during_reconfiguration=True,
                    error_mapper=_application_error_mapping,
                )
            )
        return registry

    def _worktree_settings_response(self) -> method_dtos.SettingsReadResponseDto:
        if self.worktree_retention is None:
            raise ApplicationError("INTERNAL_ERROR", "Worktree settings unavailable")
        settings = self.worktree_retention.settings.read()
        return method_dtos.SettingsReadResponseDto.model_validate(
            {
                "automaticCleanup": settings.automatic_cleanup,
                "managedWorktreeLimit": settings.managed_worktree_limit,
                "updatedAt": int(settings.updated_at.timestamp() * 1000),
            }
        )

    def _worktree_settings_update(
        self, request: method_dtos.SettingsUpdateRequestDto
    ) -> method_dtos.SettingsUpdateResponseDto:
        if self.worktree_retention is None:
            raise ApplicationError("INTERNAL_ERROR", "Worktree settings unavailable")
        try:
            settings = self.worktree_retention.settings.update(
                automatic_cleanup=request.automatic_cleanup,
                managed_worktree_limit=request.managed_worktree_limit,
            )
        except ValueError as error:
            raise ApplicationInvalidParamsError("INVALID_PARAMS", str(error)) from error
        return method_dtos.SettingsUpdateResponseDto.model_validate(
            {
                "automaticCleanup": settings.automatic_cleanup,
                "managedWorktreeLimit": settings.managed_worktree_limit,
                "updatedAt": int(settings.updated_at.timestamp() * 1000),
            }
        )

    def _planning_application(self) -> PlanningApplication:
        return PlanningApplication(self.store, self.supervisor.schedule_next,
                                   self.supervisor.events.deliver_pending, self._scan_text)

    def _channel_read(self, request: ChannelReadRequest) -> ChannelResponse:
        if self.channel_service is None:
            raise ApplicationError("INVALID_STATE", "channel service unavailable")
        return self.channel_service.read(request.channel)

    def _channel_save(self, request: ChannelSaveRequest) -> ChannelResponse:
        if self.channel_service is None:
            raise ApplicationError("INVALID_STATE", "channel service unavailable")
        try:
            self.channel_service.application.save(request)
        except (ChannelError, OSError) as error:
            raise ApplicationError("INVALID_PARAMS", "渠道保存失败，请检查配置、模型、项目及凭证文件权限。") from error
        return self.channel_service.read(request.channel)

    def _automation_application(self) -> AutomationApplication:
        if self.store.automations is None:
            applications = self._applications_or_error()
            self.store.automations = AutomationApplication(self.store,
                resolve_model=applications.runs.select_automation_model,
                publish=self.supervisor.events.deliver_pending, launch=self._launch_automation,
                scan_text=self._scan_text)
        return self.store.automations

    def _automation_history(self, request: AutomationHistoryRequest) -> AutomationResponse:
        repository = self._automation_application().repository
        repository.read(request.id)
        try:
            after = int(request.cursor or "0")
            if after < 0:
                raise ValueError
        except ValueError:
            raise AutomationError("invalid_arguments", "分页游标无效。") from None
        items, cursor = repository.history(request.id, after)
        return AutomationResponse(kind="history", occurrences=items, next_cursor=cursor)

    def _refresh_automation_schedule(self) -> AutomationResponse:
        if self.automation_scheduler is not None:
            self.automation_scheduler.refresh()
        return AutomationResponse(kind="list")

    def _launch_automation(self, occurrence_id: str) -> None:
        application = self._automation_application()
        def execute(cancel: threading.Event) -> None:
            occurrence = application.repository.occurrence(occurrence_id)
            task = occurrence.snapshot
            try:
                if cancel.is_set():
                    raise AutomationError("automation_canceled", "执行准备已停止。")
                applications = self._applications_or_error()
                application.resolve_model(task.model)
                session_id = task.target.session_id
                if session_id is None:
                    from uuid import uuid5, NAMESPACE_URL
                    root = application.repository.project_root(task.target.project_id) if task.target.project_id else None
                    session = applications.sessions.create(method_dtos.SessionCreateRequestDto(
                        operationId=str(uuid5(NAMESPACE_URL, "automation/session/" + occurrence.id)),
                        workspaceRoot=root, executionMode=task.target.execution.execution_mode,
                        baseRef=task.target.execution.base_ref,
                        workspaceNameHint=task.name if root is None else None,
                    ))
                    session_id = session.id
                    application.repository.prepared_session(occurrence.id, session_id)
                    self.store.rename_session(session_id, task.session_title())
                if cancel.is_set() or self.supervisor.control_state is not RuntimeControlState.RUNNING:
                    raise AutomationError("automation_canceled", "执行准备已停止。")
                outcome = applications.runs.start(method_dtos.RunStartRequestDto(
                    sessionId=session_id, userInput=task.prompt, modelId=task.model.model_id,
                    reasoningSelection=task.model.reasoning_selection, approvalMode=task.approval_mode,
                    fullAccessConfirmation=task.full_access_confirmation,
                ), automation_occurrence_id=occurrence.id)
                outcome.release_after_commit()
                self.supervisor.events.deliver_pending()
            except Exception as error:
                logger.exception("Scheduled task preparation failed: %s", task.id)
                code = error.code if isinstance(error, (ApplicationError, AutomationError)) else ""
                reasons = {
                    "MODEL_NOT_AVAILABLE": "任务模型已不可用，请重新选择模型。",
                    "INVALID_PARAMS": "任务的思考配置已不适用于当前模型，请重新选择。",
                    "RESOURCE_NOT_FOUND": "目标会话或项目已不存在。",
                    "WORKSPACE_IDENTITY_CHANGED": "工作区身份已改变，请检查执行目录。",
                    "WORKSPACE_BOUNDARY_VIOLATION": "执行目录不符合工作区要求，请重新选择。",
                    "GIT_WORKTREE_MISSING": "本地工作树已清理，请先恢复工作树。",
                    "WORKTREE_RESTORE_REQUIRED": "本地工作树需要恢复，请先处理原会话。",
                    "GIT_WORKTREE_INVALID": "工作树或起始分支不可用，请检查执行配置。",
                }
                reason = str(error) if isinstance(error, AutomationError) else reasons.get(code, "任务准备失败，请检查执行位置和 Runtime 日志。")
                application.repository.finish(occurrence_id, reason, pause=code in {
                    "MODEL_NOT_AVAILABLE", "INVALID_PARAMS", "RESOURCE_NOT_FOUND", "WORKSPACE_IDENTITY_CHANGED", "WORKSPACE_BOUNDARY_VIOLATION",
                    "GIT_WORKTREE_MISSING", "WORKTREE_RESTORE_REQUIRED", "GIT_WORKTREE_INVALID",
                    "automation_target_unavailable", "automation_verification_required",
                }, skipped=code in {"automation_revision_conflict", "automation_session_busy", "automation_canceled"})
                self.supervisor.events.deliver_pending()
        if not self.supervisor.start_managed_task("automation-prepare", execute):
            application.repository.finish(occurrence_id, "应用正在关闭或重新配置。")

    def _agent_definitions(self) -> AgentDefinitionsApplication:
        return AgentDefinitionsApplication(AgentCatalog(self.store.database.data_directory, self.model_config))

    def _input_application(self) -> InputContextApplication:
        return InputContextApplication(self.store, self._applications_or_error().extensions, self._scan_text, AgentCatalog(self.store.database.data_directory, self.model_config))

    def _applications_or_error(self) -> _RuntimeApplications:
        """Return initialized use cases, retaining no second state authority."""

        if self._applications is None:
            if self.store.health_state != "ready":
                raise ApplicationError("STORAGE_HEALTH_ONLY")
            self._applications = self._build_applications()
        return self._applications

    def _build_applications(self) -> _RuntimeApplications:
        if self.worktree_manager is None:
            self.worktree_manager = WorktreeManager(self.store.database)
        task_lifecycle = TaskLifecycleApplication(
            _ServerTaskLifecycleRuntime(self.supervisor)
        )
        session_lifecycle = SessionLifecycleCoordinator()
        sessions = SessionApplication(
            self.store,
            scan_text=self._scan_text,
            worktree_manager=self.worktree_manager,
            lifecycle=session_lifecycle,
            retention=self.worktree_retention,
            repository_runtime=self.repository_runtime,
        )
        return _RuntimeApplications(
            search=SearchApplication(SearchRepository(self.store.database), SessionRepository(self.store.database)),
            queued_runs=QueuedRunApplication(
                QueuedRunRepository(self.store.database), self.supervisor.events,
                self._scan_text, self._discard_model_config,
            ),
            sidebar=SidebarApplication(SidebarRepository(self.store.database), self.supervisor.events),
            projects=ProjectApplication(
                self.worktree_manager.repository,
                lifecycle=session_lifecycle,
                create_project=self.worktree_manager.create_project,
                cleanup_empty_sessions=sessions.cleanup_empty_sessions_for_project,
            ),
            sessions=sessions,
            session_forks=SessionForkApplication(
                SessionHistoryRepository(self.store.database), sessions, session_lifecycle,
                self.supervisor.events, self.worktree_manager,
                self.worktree_retention.snapshot_service if self.worktree_retention else None,
            ),
            runs=RunApplication(
                agent_catalog=AgentCatalog(self.store.database.data_directory, self.model_config),
                store=self.store,
                runtime=self.supervisor,
                environment=_ServerRunEnvironment(self),
                lifecycle=task_lifecycle,
                scan_text=self._scan_text,
                worktree_manager=self.worktree_manager,
                session_repository=self.store.typed_runtime_repository(),
                lifecycle_coordinator=session_lifecycle,
                repository_runtime=self.repository_runtime,
            ),
            approvals=ApprovalApplication(
                self.store.typed_runtime_repository(),
                _ServerApprovalRuntime(self.supervisor),
            ),
            models=ModelApplication(self.model_config),
            extensions=ExtensionApplication(
                store=self.store,
                plugins=lambda: self.plugins,
            ),
            repository_factory=self.repository_factory,
            repository_runtime=self.repository_runtime,
            context=ContextApplication(
                snapshots=self.store.context_snapshot_repository(),
                verified_compactions=self.store.verified_compaction_repository(),
                management=ContextManagementRepository(self.store.database),
            ),
            checkpoints=CheckpointApplication(
                self.store,
                self.store.checkpoint_repository(),
                worktree_manager=self.worktree_manager,
                lifecycle=session_lifecycle,
                retention=self.worktree_retention,
            ),
            task_lifecycle=task_lifecycle,
            workspace=WorkspaceExplorerApplication(
                self.store.typed_runtime_repository(),
                worktree_manager=self.worktree_manager,
                scan_text=self._scan_text,
                on_changes=lambda session_id, changes: self.send({
                    "jsonrpc": "2.0",
                    "method": "workspace/changed",
                    "params": {
                        "sessionId": session_id,
                        "paths": [change.path for change in changes],
                    },
                }),
            ),
            review=ReviewApplication(
                ReviewCommentRepository(self.store.database),
                git=sessions,
                scan_text=self._scan_text,
            ),
        )

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

        startup_started = time.perf_counter()
        try:
            with startup_phase("storage"):
                self.store.initialize()
            if self.store.health_state == "ready":
                with startup_phase("runtime_dependencies"):
                    self.runtime_dependency_catalog = discover_runtime_dependency_catalog()
                    self.supervisor.bind_runtime_dependency_catalog(self.runtime_dependency_catalog)
                self._attach_storage_logging()
                self.worktree_manager = WorktreeManager(self.store.database)
                with startup_phase("worktree_recovery"):
                    self.worktree_manager.recover()
                self.worktree_retention = WorktreeRetentionService(
                    self.store.database, self.worktree_manager
                )
                try:
                    with startup_phase("worktree_retention"):
                        self.worktree_retention.reconcile()
                except Exception:
                    logger.exception("Worktree retention recovery is incomplete")
                with startup_phase("sensitive_scanner"):
                    self.sensitive = SensitiveScanner()
                assert self.store.data_directory is not None
                with startup_phase("skills"):
                    deploy_system_skills(self.store.data_directory)
                    self.plugins = PluginCatalog(self.store)
                    SkillManagement(SkillCatalog(self.plugins)).cleanup()
                with startup_phase("model_config"):
                    self.model_config.initialize()
                self.async_kernel = RuntimeAsyncKernel(
                    resource_registry=self.supervisor.resources,
                )
                with startup_phase("async_kernel"):
                    self.async_kernel.start()
                self.model_gateway = ModelGateway(
                    self.model_config,
                    async_kernel=self.async_kernel,
                    resource_registry=self.supervisor.resources,
                )
                self.supervisor.bind_async_kernel(self.async_kernel)
                with startup_phase("restart_verification"):
                    self.supervisor.verify_restart_state()
                with startup_phase("applications_and_handoffs"):
                    self._applications = self._build_applications()
                    self._applications.sessions.recover_handoffs()
                applications = self._applications_or_error()
                self.channel_service = ChannelService(ChannelApplication(ChannelRepository(self.store.database),
                    ChannelSecrets(self.store.data_directory), applications.sessions, applications.runs, self.model_config),
                    lambda: self.initialized and self.supervisor.control_state is RuntimeControlState.RUNNING,
                    self.supervisor.events.deliver_pending,
                    lambda target: self.supervisor.start_managed_task("channel-prepare", target))
                self.supervisor.start_channel_service(self.channel_service.run)
                application = self._automation_application()
                self.automation_scheduler = AutomationScheduler(application.repository, self._launch_automation,
                    lambda: self.initialized and self.supervisor.control_state is RuntimeControlState.RUNNING,
                    self.supervisor.events.deliver_pending)
                self.supervisor.start_automation_service(self.automation_scheduler.run)
                self.store.database.memory.on_cleanup = self.store.collect_unreferenced_blobs
                self.memory_jobs = MemoryJobs(
                    self.store.database.memory, self.model_config, self.model_gateway,
                    lambda: (
                        not self.initialized
                        or self.supervisor.has_active_workers()
                        or self.supervisor.control_state is not RuntimeControlState.RUNNING
                    ),
                    idle_maintenance=self._idle_maintenance,
                )
                self.supervisor.start_managed_task("memory", self.memory_jobs.run, persistent=True)
        except (
            StorageError,
            ModelConfigError,
            SensitiveScanError,
            RuntimeDependencyCatalogError,
            SkillReadError,
            OSError,
            RuntimeError,
        ):
            self._close_async_kernel()
            self._detach_storage_logging()
            logger.exception("Runtime storage initialization failed")
            self.send(business_error(request_id, "INTERNAL_ERROR"))
            return

        if self.store.health_state == "ready":
            with startup_phase("seatbelt_self_test"):
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
                            or bool(self.model_config.list())
                        ),
                    },
                },
            )
        )
        if self.store.health_state == "ready":
            with startup_phase("pending_events"):
                self.supervisor.events.deliver_pending()
        logger.info("Runtime initialized duration_ms=%.3f", (time.perf_counter() - startup_started) * 1000)
        self.supervisor.schedule_next()

    def _idle_maintenance(self, cancel: threading.Event) -> None:
        if self.store.maintain_repository_cache(cancel):
            self.repository_runtime.discard_cached_generations()
        if not cancel.is_set():
            self.store.collect_unreferenced_blobs(cancel)

    def shutdown(self, request_id: str, params: object) -> None:
        if not isinstance(params, dict) or params:
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        if not self.initialized:
            self.repository_runtime.shutdown_all()
            self.send(response(request_id, {}))
            self.supervisor.lifecycle = RuntimeLifecycle.CLOSED
            self.shutting_down = True
            return
        try:
            self.supervisor.shutdown()
            self.repository_runtime.shutdown_all()
            self._close_workspace_explorer()
            self._cleanup_extensions()
            self._close_async_kernel()
            if self.store.health_state == "ready":
                self.store.cancel_active_async_operations()
                self.supervisor.events.deliver_pending()
                if self.store.pending_outbox_count():
                    raise ResourceRegistryError("event delivery is not quiescent")
            self.supervisor.resources.ensure_empty()
        except (
            RuntimeShutdownTimeout,
            RepositoryWatcherShutdownError,
            AsyncKernelCloseError,
            ResourceRegistryError,
        ):
            self.send(business_error(request_id, "RUNTIME_SHUTDOWN_TIMEOUT"))
            return
        self._clear_frozen_model_configs()
        self._detach_storage_logging()
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
            self.repository_runtime.shutdown_all()
            self._close_async_kernel()
            self._clear_frozen_model_configs()
            self._detach_storage_logging()
            self.store.close()
            self.supervisor.lifecycle = RuntimeLifecycle.CLOSED
            return
        try:
            self.supervisor.shutdown()
            self.repository_runtime.shutdown_all()
            self._close_workspace_explorer()
            self._cleanup_extensions()
            self._close_async_kernel()
            self.store.cancel_active_async_operations()
            self.supervisor.events.deliver_pending()
            if self.store.pending_outbox_count():
                raise ResourceRegistryError("event delivery is not quiescent")
            self.supervisor.resources.ensure_empty()
        except (
            RuntimeShutdownTimeout,
            RepositoryWatcherShutdownError,
            AsyncKernelCloseError,
            ResourceRegistryError,
        ):
            raise
        self._clear_frozen_model_configs()
        self._detach_storage_logging()
        self.store.close()
        self.supervisor.lifecycle = RuntimeLifecycle.CLOSED

    def _attach_storage_logging(self) -> None:
        if self._storage_log_handler is not None:
            return
        handler = self.store.create_log_handler()
        logging.getLogger().addHandler(handler)
        self._storage_log_handler = handler

    def _detach_storage_logging(self) -> None:
        handler = self._storage_log_handler
        if handler is None:
            return
        self._storage_log_handler = None
        logging.getLogger().removeHandler(handler)
        handler.close()

    def _close_workspace_explorer(self) -> None:
        if self._applications is not None:
            self._applications.workspace.close()

    def _model_lease_for(self, model_id: str) -> ModelClientLease:
        if self.model is not None:
            return ModelClientLease(
                self.model,
                resource_registry=self.supervisor.resources,
                owner_id=model_id,
            )
        config = self.model_config.get(model_id)
        if config is None or self.model_gateway is None:
            raise ModelConfigError("model is not configured")
        return self.model_gateway.acquire_lease(config)

    def _model_lease_for_run(self, run_id: str) -> ModelClientLease:
        run = self.store.read_run(run_id)
        profile = self.store.read_model_profile(run_id)
        if self.model is not None:
            return self._model_lease_for(str(run["modelId"]))
        with self._frozen_model_configs_lock:
            config = self._frozen_model_configs.pop(run_id, None)
        if config is None:
            config = self.model_config.get(str(run["modelId"]))
            if config is None:
                raise ModelConfigError("model is not configured")
        if self.model_gateway is None:
            raise ModelConfigError("model gateway is unavailable")
        return self.model_gateway.acquire_lease(
            config, reasoning_selection=profile.reasoning_selection
        )

    def _freeze_model_config(self, run_id: str, config: ModelConfig) -> None:
        with self._frozen_model_configs_lock:
            self._frozen_model_configs[run_id] = config

    def _discard_model_config(self, run_id: str) -> None:
        with self._frozen_model_configs_lock:
            self._frozen_model_configs.pop(run_id, None)

    def _clear_frozen_model_configs(self) -> None:
        with self._frozen_model_configs_lock:
            self._frozen_model_configs.clear()

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
            logger.warning("Session title generation failed: %s", type(error).__name__)
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
            SkillManagement(SkillCatalog(self.plugins)).cleanup()


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
        return (
            self.cancel.wait(remaining if timeout is None else min(timeout, remaining))
            or self.is_set()
        )

    @property
    def timed_out(self) -> bool:
        return time.monotonic() >= self.deadline


def _model_from_environment() -> ModelClient | None:
    fixture = os.environ.get("EIDOS_FAKE_MODEL")
    if fixture == "write":
        return ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        ModelToolCall(
                            "fake-write-1",
                            "apply_patch",
                            {
                                "patch": (
                                    "*** Begin Patch\n"
                                    "*** Add File: approved.txt\n"
                                    "+approved\n"
                                    "*** End Patch\n"
                                )
                            },
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
                            {"command": "printf desktop-shell-ok", "yieldTimeMs": 5000},
                        ),
                    )
                ),
                ModelResponse(text="Fake model completed after the approved command."),
            ]
        )
    if fixture == "network-shell":
        return ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        ModelToolCall(
                            "fake-network-shell-1",
                            "run_shell",
                            {
                                "command": "printf approved-network-shell",
                                "sandboxPermissions": "with_additional_permissions",
                                "additionalPermissions": {
                                    "network": {"enabled": True},
                                },
                                "justification": "Test process recovery during approval",
                            },
                        ),
                    )
                ),
                ModelResponse(text="Fake model completed after network approval."),
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
