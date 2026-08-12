from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
import time
from typing import Protocol, TypeVar

from pydantic import ValidationError

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.session_lifecycle import SessionLifecycleCoordinator
from eidos_runtime.context.budget import ContextUsageSnapshot
from eidos_runtime.context.plan import ContextSnapshot
from eidos_runtime.application.task_lifecycle import (
    LifecycleAction,
    TaskLifecycleApplication,
)
from eidos_runtime.db.database import Database, WorkspaceIdentity
from eidos_runtime.db.errors import (
    InvalidRunStateError,
    OperationConflictError,
    OperationInProgressError,
    ResourceNotFoundError,
    WorkspaceBoundaryError,
    WorkspaceIdentityChangedError,
)
from eidos_runtime.domain.session import SessionProjection
from eidos_runtime.git.errors import WorktreeError
from eidos_runtime.model.client import ModelClient, ModelProfileSnapshot, ModelUsage
from eidos_runtime.model.config import (
    ModelConfig,
    ModelConfigError,
    MODEL_CATALOG,
    default_profile_snapshot,
)
from eidos_runtime.persistence.repositories import TypedRuntimeRepository
from eidos_runtime.persistence.errors import ConditionalUpdateFailed
from eidos_runtime.domain.run import Run
from eidos_runtime.domain.long_task import LongTaskProgress
from eidos_runtime.protocol.methods import (
    MethodResultDto,
    RunCancelRequestDto,
    RunCancelResponseDto,
    RunPauseRequestDto,
    RunPauseResponseDto,
    RunResumeRequestDto,
    RunResumeResponseDto,
    RunStartRequestDto,
    RunStartResponseDto,
    RunStatusRequestDto,
    RunStatusResponseDto,
    ContextUsageRequestDto,
    ContextUsageResponseDto,
)
from eidos_runtime.runtime.supervisor import (
    RunCancelTimeout,
    RunReconciliationRequired,
)
from eidos_runtime.sandbox.sensitive import (
    SensitiveContentDenied,
    SensitiveScanError,
)


ResultT = TypeVar("ResultT", bound=MethodResultDto)


class RunStorePort(Protocol):
    """The public compatibility authority required for Run start/cancel."""

    def read_session(self, session_id: str) -> dict[str, object] | None: ...

    def session_model_id(self, session_id: str) -> str | None: ...

    def operation_result(
        self, operation_id: str, scope: str, request: dict[str, object]
    ) -> object | None: ...

    def enqueue_run(
        self,
        session_id: str,
        user_input: str,
        *,
        operation_id: str | None = None,
        session_title: str | None = None,
        model_id: str,
        model_profile: ModelProfileSnapshot | None = None,
        extension_snapshot: dict[str, object] | None = None,
        expected_workspace_identity: WorkspaceIdentity | None = None,
    ) -> tuple[dict[str, object], dict[str, object]]: ...

    def read_run(self, run_id: str) -> dict[str, object]: ...

    def workspace_for_run(self, run_id: str) -> WorkspaceIdentity: ...

    def workspace_for_session(self, session_id: str) -> WorkspaceIdentity: ...

    def read_model_profile(self, run_id: str) -> ModelProfileSnapshot: ...

    def latest_model_usage(self, run_id: str) -> ModelUsage | None: ...

    def read_latest_context_snapshot(self, run_id: str) -> ContextSnapshot | None: ...

    def interrupt_run(self, run_id: str) -> dict[str, object]: ...

    def long_task_progress(self, run_id: str) -> LongTaskProgress | None: ...


class RepositoryWorkspaceRuntimePort(Protocol):
    def activate_workspace(self, root: Path) -> object: ...


class RunSessionRepositoryPort(Protocol):
    def read_session_projection(self, session_id: str) -> SessionProjection | None: ...


class RunWorktreePort(Protocol):
    def execution_identity(self, worktree_id: str) -> WorkspaceIdentity: ...

    def touch_last_used(self, worktree_id: str) -> object: ...


class RunRuntimePort(Protocol):
    """Runtime lifecycle operations owned by RunSupervisor.

    The opaque start token deliberately remains owned by the supervisor.  The
    application only holds it until protocol output confirms success or fails.
    """

    def prepare_next(self) -> object | None: ...

    def release(self, start: object | None) -> None: ...

    def abort(self, start: object | None) -> None: ...

    def cancel_run(
        self, run_id: str, *, operation_id: str | None = None
    ) -> dict[str, object]: ...


class RunStartEnvironmentPort(Protocol):
    """Non-durable collaborators needed to materialize one Run start."""

    def model_is_configured(self) -> bool: ...

    def model_config(self, model_id: str) -> ModelConfig: ...

    def model_for(self, model_id: str) -> ModelClient: ...

    def freeze_model_config(self, run_id: str, config: ModelConfig) -> None: ...

    def discard_model_config(self, run_id: str) -> None: ...

    def extension_snapshot(self) -> dict[str, object]: ...

    def schedule_title_generation(
        self, session_id: str, user_input: str, model_id: str
    ) -> None: ...


@dataclass(frozen=True)
class _TitleGenerationRequest:
    session_id: str
    user_input: str
    model_id: str


@dataclass
class RunStartOutcome:
    """A typed response plus the one-shot worker-gate acknowledgement.

    ``mark_response_delivered`` and ``mark_response_failed`` are invoked by
    the protocol adapter after the physical JSON-RPC write succeeds or fails.
    This preserves the legacy invariant that a claimed worker cannot run if
    its start response was not delivered.
    """

    response: RunStartResponseDto
    _store: RunStorePort
    _runtime: RunRuntimePort
    _start: object | None
    _run_id: str
    _environment: RunStartEnvironmentPort
    _title_request: _TitleGenerationRequest | None = None
    _has_frozen_model_config: bool = False
    _settled: bool = field(default=False, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def mark_response_delivered(self) -> None:
        self._mark_settled()
        if self._start is not None:
            self._runtime.release(self._start)
        if self._title_request is not None:
            self._environment.schedule_title_generation(
                self._title_request.session_id,
                self._title_request.user_input,
                self._title_request.model_id,
            )

    def mark_response_failed(self) -> None:
        self._mark_settled()
        if self._has_frozen_model_config:
            self._environment.discard_model_config(self._run_id)
        if self._start is not None:
            try:
                self._runtime.abort(self._start)
                try:
                    self._store.interrupt_run(self._run_id)
                except (ResourceNotFoundError, InvalidRunStateError):
                    pass
            finally:
                self._runtime.release(self._start)

    def _mark_settled(self) -> None:
        with self._lock:
            if self._settled:
                raise RuntimeError("Run start response outcome is already settled")
            self._settled = True


class RunApplication:
    """Owns top-level Run start/cancel use cases.

    SQLite remains the compatibility write authority and RunSupervisor remains
    the only runtime-state authority.  The application coordinates their
    public ports and returns validated method-specific results.
    """

    def __init__(
        self,
        database: Database | None = None,
        *,
        store: RunStorePort | None = None,
        runtime: RunRuntimePort | None = None,
        environment: RunStartEnvironmentPort | None = None,
        lifecycle: TaskLifecycleApplication | None = None,
        scan_text: Callable[[str], str] | None = None,
        worktree_manager: RunWorktreePort | None = None,
        session_repository: RunSessionRepositoryPort | None = None,
        lifecycle_coordinator: SessionLifecycleCoordinator | None = None,
        repository_runtime: RepositoryWorkspaceRuntimePort | None = None,
    ) -> None:
        self._typed_repository = (
            TypedRuntimeRepository(database) if database is not None else None
        )
        self._store = store
        self._runtime = runtime
        self._environment = environment
        self._lifecycle = (
            lifecycle
            if lifecycle is not None
            else TaskLifecycleApplication(runtime)
            if runtime is not None
            else None
        )
        self._scan_text = scan_text
        self._worktree_manager = worktree_manager
        self._session_repository = session_repository
        self._repository_runtime = repository_runtime
        self._session_lifecycle = (
            lifecycle_coordinator or SessionLifecycleCoordinator()
        )

    def read(self, run_id: str) -> Run | None:
        if self._typed_repository is None:
            raise RuntimeError("RunApplication read repository is not configured")
        return self._typed_repository.read_run(run_id)

    def list(self, session_id: str) -> tuple[Run, ...]:
        if self._typed_repository is None:
            raise RuntimeError("RunApplication read repository is not configured")
        return self._typed_repository.list_runs(session_id)

    def start(self, request: RunStartRequestDto) -> RunStartOutcome:
        store, runtime, environment, scan_text = self._start_dependencies()
        if not environment.model_is_configured():
            raise ApplicationError("INVALID_STATE", "no model is configured")
        try:
            user_input = scan_text(request.user_input)
        except SensitiveContentDenied as error:
            raise ApplicationError("SENSITIVE_CONTENT_REJECTED", str(error)) from error
        except SensitiveScanError as error:
            raise ApplicationError("SENSITIVE_SCAN_FAILED", str(error)) from error

        session = store.read_session(request.session_id)
        if session is None:
            raise ApplicationError("RESOURCE_NOT_FOUND", "session not found")

        model_id, model_profile, model_config = self._select_model(
            request,
            store=store,
            environment=environment,
        )
        extension_snapshot = environment.extension_snapshot()
        operation_request: dict[str, object] = {
            "sessionId": request.session_id,
            "userInput": user_input,
            "modelId": model_id,
            "extensionSnapshot": extension_snapshot,
        }
        if request.operation_id is not None:
            try:
                replay = store.operation_result(
                    request.operation_id,
                    "run/start",
                    operation_request,
                )
            except OperationConflictError as error:
                raise ApplicationError("OPERATION_ID_REUSED", str(error)) from error
            except OperationInProgressError as error:
                raise ApplicationError("OPERATION_IN_PROGRESS", str(error)) from error
            if isinstance(replay, dict) and isinstance(replay.get("run"), dict):
                response = _result(RunStartResponseDto, replay["run"])
                return RunStartOutcome(
                    response=response,
                    _store=store,
                    _runtime=runtime,
                    _start=None,
                    _run_id=str(response.root["id"]),
                    _environment=environment,
                )

        try:
            with self._session_lifecycle.hold(request.session_id):
                current_session = store.read_session(request.session_id)
                if current_session is None:
                    raise ResourceNotFoundError("session not found")
                expected_workspace_identity = self._admit_session_workspace(
                    request.session_id
                )
                workspace = store.workspace_for_session(request.session_id)
                if expected_workspace_identity is None:
                    expected_workspace_identity = workspace
                elif expected_workspace_identity != workspace:
                    raise WorkspaceIdentityChangedError("workspace_identity_changed")
                if self._repository_runtime is not None:
                    self._repository_runtime.activate_workspace(workspace.path)
                needs_title = "title" not in current_session
                created, _user_item = store.enqueue_run(
                    request.session_id,
                    user_input,
                    operation_id=request.operation_id,
                    session_title=None,
                    model_id=model_id,
                    model_profile=model_profile,
                    extension_snapshot=extension_snapshot,
                    expected_workspace_identity=expected_workspace_identity,
                )
        except ResourceNotFoundError as error:
            raise ApplicationError("RESOURCE_NOT_FOUND", str(error)) from error
        except WorktreeError as error:
            raise ApplicationError(
                _run_worktree_error_code(error), str(error)
            ) from error
        except WorkspaceIdentityChangedError as error:
            raise ApplicationError(
                "WORKSPACE_IDENTITY_CHANGED", str(error)
            ) from error
        except WorkspaceBoundaryError as error:
            raise ApplicationError(
                "WORKSPACE_BOUNDARY_VIOLATION", str(error)
            ) from error
        except OperationConflictError as error:
            raise ApplicationError("OPERATION_ID_REUSED", str(error)) from error
        except OperationInProgressError as error:
            raise ApplicationError("OPERATION_IN_PROGRESS", str(error)) from error

        run_id = str(created["id"])
        if model_config is not None:
            environment.freeze_model_config(run_id, model_config)
        try:
            start = runtime.prepare_next()
        except Exception:
            if model_config is not None:
                environment.discard_model_config(run_id)
            raise
        try:
            response = _result(RunStartResponseDto, store.read_run(run_id))
        except Exception:
            self._abort_before_response(store, runtime, start, run_id)
            if model_config is not None:
                environment.discard_model_config(run_id)
            raise
        return RunStartOutcome(
            response=response,
            _store=store,
            _runtime=runtime,
            _start=start,
            _run_id=run_id,
            _environment=environment,
            _has_frozen_model_config=model_config is not None,
            _title_request=(
                _TitleGenerationRequest(
                    session_id=request.session_id,
                    user_input=user_input,
                    model_id=model_id,
                )
                if needs_title
                else None
            ),
        )

    def _admit_session_workspace(
        self, session_id: str
    ) -> WorkspaceIdentity | None:
        manager = self._worktree_manager
        if manager is None:
            return None
        repository = self._session_repository
        if repository is None:
            raise ApplicationError(
                "INTERNAL_ERROR", "managed Run admission repository is unavailable"
            )
        projection = repository.read_session_projection(session_id)
        if projection is None:
            raise ResourceNotFoundError("session not found")
        if projection.worktree is None:
            return None
        identity = manager.execution_identity(projection.worktree.worktree_id)
        touch = getattr(manager, "touch_last_used", None)
        if touch is not None:
            touch(projection.worktree.worktree_id)
        return identity

    def cancel(self, request: RunCancelRequestDto) -> RunCancelResponseDto:
        store, _runtime = self._cancel_dependencies()
        try:
            current = store.read_run(request.run_id)
        except ResourceNotFoundError as error:
            raise ApplicationError("RESOURCE_NOT_FOUND", str(error)) from error
        if current.get("status") not in {
            "queued",
            "running",
            "waiting_approval",
            "finalizing",
            "canceled",
        }:
            raise ApplicationError("INVALID_STATE", "run cannot be canceled")
        try:
            if self._lifecycle is None:
                raise RuntimeError("Run lifecycle application is not configured")
            self._lifecycle.execute(
                LifecycleAction.CANCEL,
                request.run_id,
                operation_id=request.operation_id,
            )
            current = store.read_run(request.run_id)
        except InvalidRunStateError:
            try:
                current = store.read_run(request.run_id)
            except ResourceNotFoundError as error:
                raise ApplicationError("RESOURCE_NOT_FOUND", str(error)) from error
            if current.get("status") != "canceled":
                raise ApplicationError("INVALID_STATE", "run cannot be canceled")
        except RunCancelTimeout as error:
            raise ApplicationError("RUN_CANCEL_TIMEOUT", str(error)) from error
        except RunReconciliationRequired as error:
            raise ApplicationError("RUN_RECONCILIATION_REQUIRED", str(error)) from error
        except OperationConflictError as error:
            raise ApplicationError("OPERATION_ID_REUSED", str(error)) from error
        except OperationInProgressError as error:
            raise ApplicationError("OPERATION_IN_PROGRESS", str(error)) from error
        return _result(RunCancelResponseDto, current)

    def status(self, request: RunStatusRequestDto) -> RunStatusResponseDto:
        return self._lifecycle_response(
            RunStatusResponseDto, request.run_id, action=None
        )

    def context_usage(
        self, request: ContextUsageRequestDto
    ) -> ContextUsageResponseDto:
        store, _runtime = self._cancel_dependencies()
        try:
            profile = store.read_model_profile(request.run_id)
            usage = _context_usage_snapshot(
                profile.context_window_tokens,
                store.latest_model_usage(request.run_id),
                store.read_latest_context_snapshot(request.run_id),
            )
        except ResourceNotFoundError as error:
            raise ApplicationError("RESOURCE_NOT_FOUND", str(error)) from error
        return ContextUsageResponseDto(
            contextUsage=(
                usage.model_dump(mode="json", by_alias=True)
                if usage is not None
                else None
            )
        )

    def pause(self, request: RunPauseRequestDto) -> RunPauseResponseDto:
        return self._lifecycle_response(
            RunPauseResponseDto,
            request.run_id,
            action=LifecycleAction.PAUSE,
            operation_id=request.operation_id,
        )

    def resume(self, request: RunResumeRequestDto) -> RunResumeResponseDto:
        return self._lifecycle_response(
            RunResumeResponseDto,
            request.run_id,
            action=LifecycleAction.RESUME,
            operation_id=request.operation_id,
        )

    def _lifecycle_response(
        self,
        result_type: type[
            RunStatusResponseDto | RunPauseResponseDto | RunResumeResponseDto
        ],
        run_id: str,
        *,
        action: LifecycleAction | None,
        operation_id: str | None = None,
    ) -> RunStatusResponseDto | RunPauseResponseDto | RunResumeResponseDto:
        store, _runtime = self._cancel_dependencies()
        try:
            if self._lifecycle is None:
                raise RuntimeError("Run lifecycle application is not configured")
            if action is not None:
                self._lifecycle.execute(action, run_id, operation_id=operation_id)
            progress = self._lifecycle.status(run_id)
            run = store.read_run(run_id)
        except ResourceNotFoundError as error:
            raise ApplicationError("RESOURCE_NOT_FOUND", str(error)) from error
        except (InvalidRunStateError, ConditionalUpdateFailed) as error:
            raise ApplicationError("INVALID_STATE", str(error)) from error
        payload = {
            "run": run,
            "task": progress.model_dump(by_alias=True)
            if progress is not None
            else None,
            "resumeVerification": (
                progress.last_verification.model_dump(by_alias=True)
                if progress is not None and progress.last_verification is not None
                else None
            ),
        }
        return _result(result_type, payload)

    def _select_model(
        self,
        request: RunStartRequestDto,
        *,
        store: RunStorePort,
        environment: RunStartEnvironmentPort,
    ) -> tuple[str, ModelProfileSnapshot, ModelConfig | None]:
        model_id = request.model_id
        try:
            run_model = environment.model_for(model_id)
        except ModelConfigError:
            run_model = None
        if run_model is not None:
            profile = getattr(run_model, "profile_snapshot", None)
            return model_id, profile or default_profile_snapshot(model_id), None
        try:
            config = environment.model_config(model_id)
            return (
                model_id,
                MODEL_CATALOG.profile(model_id).snapshot(config),
                config,
            )
        except (ModelConfigError, ValueError) as error:
            raise ApplicationError("MODEL_NOT_AVAILABLE", "model is unavailable") from error

    @staticmethod
    def _abort_before_response(
        store: RunStorePort,
        runtime: RunRuntimePort,
        start: object | None,
        run_id: str,
    ) -> None:
        try:
            runtime.abort(start)
            if start is not None:
                try:
                    store.interrupt_run(run_id)
                except (ResourceNotFoundError, InvalidRunStateError):
                    pass
        finally:
            runtime.release(start)

    def _start_dependencies(
        self,
    ) -> tuple[
        RunStorePort,
        RunRuntimePort,
        RunStartEnvironmentPort,
        Callable[[str], str],
    ]:
        if (
            self._store is None
            or self._runtime is None
            or self._environment is None
            or self._scan_text is None
        ):
            raise RuntimeError("RunApplication start dependencies are not configured")
        return self._store, self._runtime, self._environment, self._scan_text

    def _cancel_dependencies(self) -> tuple[RunStorePort, RunRuntimePort]:
        if self._store is None or self._runtime is None:
            raise RuntimeError("RunApplication cancel dependencies are not configured")
        return self._store, self._runtime


def _result(result_type: type[ResultT], value: object) -> ResultT:
    try:
        return result_type.model_validate(value)
    except ValidationError as error:
        raise ApplicationError(
            "INTERNAL_ERROR", "stored Run result violates its protocol contract"
        ) from error


def _run_worktree_error_code(error: WorktreeError) -> str:
    if error.code == "workspace_identity_changed":
        return "WORKSPACE_IDENTITY_CHANGED"
    if error.code in {
        "git_command_failed",
        "git_command_timeout",
        "git_observation_failed",
        "git_observation_incomplete",
    }:
        return "GIT_OBSERVATION_UNAVAILABLE"
    if error.code in {"worktree_not_found", "worktree_missing"}:
        return "GIT_WORKTREE_MISSING"
    if error.code == "worktree_restore_required":
        return "WORKTREE_RESTORE_REQUIRED"
    if error.code == "worktree_recovery_required":
        return "WORKTREE_RECOVERY_REQUIRED"
    return "GIT_WORKTREE_INVALID"


def _context_usage_snapshot(
    context_window_tokens: int,
    provider_usage: ModelUsage | None,
    latest_context_snapshot: ContextSnapshot | None,
) -> ContextUsageSnapshot | None:
    refreshed_at = int(time.time() * 1000)
    if provider_usage is not None and provider_usage.input_tokens is not None:
        active_tokens = provider_usage.input_tokens
        return ContextUsageSnapshot(
            active_tokens=active_tokens,
            context_window_tokens=context_window_tokens,
            percent_used=min(
                100.0,
                round(active_tokens / context_window_tokens * 100, 1),
            ),
            source="provider",
            updated_at=refreshed_at,
        )
    plan = getattr(latest_context_snapshot, "plan", None)
    budget = getattr(plan, "token_budget", None)
    estimated = getattr(budget, "context_usage", None)
    if isinstance(estimated, ContextUsageSnapshot):
        return estimated.model_copy(update={"updated_at": refreshed_at})
    return None


__all__ = [
    "RunApplication",
    "RunRuntimePort",
    "RunStartEnvironmentPort",
    "RunStartOutcome",
    "RunStorePort",
]
