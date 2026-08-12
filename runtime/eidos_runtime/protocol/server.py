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
from eidos_runtime.application.errors import (
    ApplicationError,
    ApplicationInvalidParamsError,
)
from eidos_runtime.application.approvals import (
    ApprovalApplication,
    ApprovalDecision as ApplicationApprovalDecision,
)
from eidos_runtime.application.context import ContextApplication
from eidos_runtime.application.checkpoints import CheckpointApplication
from eidos_runtime.application.extensions import (
    DeferredPluginImport,
    ExtensionApplication,
    PluginImportCompleted,
    PluginImportFailure,
)
from eidos_runtime.application.models import ModelApplication
from eidos_runtime.application.runs import RunApplication, RunStartOutcome
from eidos_runtime.application.repository import RepositoryApplicationFactory
from eidos_runtime.application.session_lifecycle import SessionLifecycleCoordinator
from eidos_runtime.application.sessions import (
    DeferredGitFetch,
    DeferredGitPull,
    DeferredGitPush,
    SessionApplication,
    clean_session_title,
)
from eidos_runtime.application.worktree_retention import WorktreeRetentionService
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
from eidos_runtime.model_gateway.gateway import ModelGateway
from eidos_runtime.domain.long_task import LongTaskProgress
from eidos_runtime.git.manager import WorktreeManager
from eidos_runtime.protocol import methods as method_dtos
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
from eidos_runtime.extensions.plugins import PluginCatalog
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


def _application_error_mapping(error: Exception) -> MethodErrorMapping | None:
    """Map only stable application failures at the protocol boundary."""

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


@dataclass(frozen=True)
class _RuntimeApplications:
    sessions: SessionApplication
    runs: RunApplication
    approvals: ApprovalApplication
    models: ModelApplication
    extensions: ExtensionApplication
    repository_factory: RepositoryApplicationFactory
    context: ContextApplication
    checkpoints: CheckpointApplication
    task_lifecycle: TaskLifecycleApplication


class _ServerRunEnvironment:
    """Narrow non-durable Run collaborators owned by RuntimeServer."""

    def __init__(self, server: "RuntimeServer") -> None:
        self._server = server

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
        self._frozen_model_configs: dict[str, ModelConfig] = {}
        self._frozen_model_configs_lock = threading.RLock()
        self.async_kernel: RuntimeAsyncKernel | None = None
        self.output_lock = threading.RLock()
        self.shell_available = False
        self.sensitive: SensitiveScanner | None = None
        self.plugins: PluginCatalog | None = None
        self.worktree_manager: WorktreeManager | None = None
        self.worktree_retention: WorktreeRetentionService | None = None
        self._applications: _RuntimeApplications | None = None
        self.supervisor = RunSupervisor(
            self.store,
            self._model_lease_for_run,
            self.send,
            self._scan_text,
            lambda: self.model is not None or bool(self.model_config.list()),
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
            (
                "project/gitContext",
                method_dtos.GitContextRequestDto,
                method_dtos.GitContextResponseDto,
                lambda _id, request: self._applications_or_error().sessions.git_context(
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
                "session/createBranch",
                method_dtos.SessionCreateBranchRequestDto,
                method_dtos.SessionCreateBranchResponseDto,
                lambda _id, request: self._applications_or_error().sessions.create_branch(
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
                "session/read",
                method_dtos.SessionReadRequestDto,
                method_dtos.SessionReadResponseDto,
                lambda _id,
                request: self._applications_or_error().sessions.read_snapshot(request),
            ),
            (
                "session/gitStatus",
                method_dtos.SessionGitStatusRequestDto,
                method_dtos.SessionGitStatusResponseDto,
                lambda _id, request: self._applications_or_error().sessions.git_status(
                    request
                ),
            ),
            (
                "session/gitDiff",
                method_dtos.SessionGitDiffRequestDto,
                method_dtos.SessionGitDiffResponseDto,
                lambda _id, request: self._applications_or_error().sessions.git_diff(
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
                "session/gitUnstage",
                method_dtos.SessionGitUnstageRequestDto,
                method_dtos.SessionGitUnstageResponseDto,
                lambda _id, request: self._applications_or_error().sessions.git_unstage(
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
            "run/start",
            "model/create",
            "model/update",
            "model/delete",
            "plugin/import",
            "plugin/setEnabled",
            "plugin/remove",
            "mcp/setEnabled",
        }
        reconfiguration_blocked = {"run/start"}
        for name, request_type, response_type, handler in handlers:
            registry.register(
                MethodRegistration(
                    name=name,
                    request_type=request_type,
                    response_type=response_type,
                    handler=_ApplicationMethodAdapter(handler, response_type),
                    allowed_when_draining=name not in draining_blocked,
                    allowed_during_reconfiguration=name not in reconfiguration_blocked,
                    error_mapper=_application_error_mapping,
                )
            )
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
        return _RuntimeApplications(
            sessions=SessionApplication(
                self.store,
                scan_text=self._scan_text,
                worktree_manager=self.worktree_manager,
                lifecycle=session_lifecycle,
                retention=self.worktree_retention,
            ),
            runs=RunApplication(
                store=self.store,
                runtime=self.supervisor,
                environment=_ServerRunEnvironment(self),
                lifecycle=task_lifecycle,
                scan_text=self._scan_text,
                worktree_manager=self.worktree_manager,
                session_repository=self.store.typed_runtime_repository(),
                lifecycle_coordinator=session_lifecycle,
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
            repository_factory=RepositoryApplicationFactory(
                self.store.repository_intelligence_repository
            ),
            context=ContextApplication(
                snapshots=self.store.context_snapshot_repository(),
                verified_compactions=self.store.verified_compaction_repository(),
            ),
            checkpoints=CheckpointApplication(
                self.store,
                self.store.checkpoint_repository(),
                worktree_manager=self.worktree_manager,
                lifecycle=session_lifecycle,
                retention=self.worktree_retention,
            ),
            task_lifecycle=task_lifecycle,
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

        try:
            self.store.initialize()
            if self.store.health_state == "ready":
                self.worktree_manager = WorktreeManager(self.store.database)
                self.worktree_manager.recover()
                self.worktree_retention = WorktreeRetentionService(
                    self.store.database, self.worktree_manager
                )
                try:
                    self.worktree_retention.reconcile()
                except Exception:
                    logger.exception("Worktree retention recovery is incomplete")
                self.sensitive = SensitiveScanner()
                assert self.store.data_directory is not None
                deploy_system_skills(self.store.data_directory)
                self.plugins = PluginCatalog(self.store)
                self.model_config.initialize()
                self.async_kernel = RuntimeAsyncKernel(
                    resource_registry=self.supervisor.resources,
                )
                self.async_kernel.start()
                self.model_gateway = ModelGateway(
                    self.model_config,
                    async_kernel=self.async_kernel,
                    resource_registry=self.supervisor.resources,
                )
                self.supervisor.bind_async_kernel(self.async_kernel)
                self.supervisor.verify_restart_state()
                self._applications = self._build_applications()
                self._applications.sessions.recover_handoffs()
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
                            or bool(self.model_config.list())
                        ),
                    },
                },
            )
        )
        if self.store.health_state == "ready":
            self.supervisor.events.deliver_pending()
        logger.info("Runtime initialized")
        self.supervisor.schedule_next()

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
            self._close_async_kernel()
            self.store.cancel_active_async_operations()
            self.supervisor.events.deliver_pending()
            if self.store.pending_outbox_count():
                raise ResourceRegistryError("event delivery is not quiescent")
            self.supervisor.resources.ensure_empty()
        except (
            RuntimeShutdownTimeout,
            AsyncKernelCloseError,
            ResourceRegistryError,
        ):
            self.send(business_error(request_id, "RUNTIME_SHUTDOWN_TIMEOUT"))
            return
        self._clear_frozen_model_configs()
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
            self._close_async_kernel()
            self._clear_frozen_model_configs()
            self.store.close()
            self.supervisor.lifecycle = RuntimeLifecycle.CLOSED
            return
        try:
            self.supervisor.shutdown()
            self._cleanup_extensions()
            self._close_async_kernel()
            self.store.cancel_active_async_operations()
            self.supervisor.events.deliver_pending()
            if self.store.pending_outbox_count():
                raise ResourceRegistryError("event delivery is not quiescent")
            self.supervisor.resources.ensure_empty()
        except (
            RuntimeShutdownTimeout,
            AsyncKernelCloseError,
            ResourceRegistryError,
        ):
            raise
        self._clear_frozen_model_configs()
        self.store.close()
        self.supervisor.lifecycle = RuntimeLifecycle.CLOSED

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
        if self.model is not None:
            return self._model_lease_for(str(run["modelId"]))
        with self._frozen_model_configs_lock:
            config = self._frozen_model_configs.pop(run_id, None)
        if config is None:
            return self._model_lease_for(str(run["modelId"]))
        if self.model_gateway is None:
            raise ModelConfigError("model gateway is unavailable")
        return self.model_gateway.acquire_lease(config)

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
