from __future__ import annotations

from eidos_runtime.persistence.input_context import InputContextRepository
from eidos_runtime.persistence.agent_catalog import AgentSnapshotRepository
from eidos_runtime.application.agent_catalog import AgentCatalog
from eidos_runtime.domain.agents import AgentCatalogSnapshot
from eidos_runtime.domain.input_reference import InputSnapshot
from eidos_runtime.domain.automation import AutomationModel, AutomationError

from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Protocol, TypeVar

from pydantic import ValidationError

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.session_lifecycle import SessionLifecycleCoordinator
from eidos_runtime.context.budget import ContextUsageSnapshot
from eidos_runtime.domain.context_management import ContextDetails
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
    StorageError,
    WorkspaceBoundaryError,
    WorkspaceIdentityChangedError,
)
from eidos_runtime.domain.session import SessionProjection
from eidos_runtime.git.errors import WorktreeError
from eidos_runtime.model.client import ModelClient, ModelProfileSnapshot
from eidos_runtime.model.config import (
    ModelConfig,
    ModelConfigError,
    ModelReasoningSelectionError,
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
)
from eidos_runtime.sandbox.sensitive import (
    SensitiveContentDenied,
    SensitiveScanError,
)


ResultT = TypeVar("ResultT", bound=MethodResultDto)


class RunStorePort(Protocol):
    """The public compatibility authority required for Run start/cancel."""

    database: Database
    data_directory: Path | None

    def read_session(self, session_id: str) -> dict[str, object] | None: ...

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
        approval_mode: str = "manual",
        work_mode: str = "execute",
        plan_id: str | None = None,
        plan_revision: int | None = None,
        extension_snapshot: dict[str, object] | None = None,
        agent_catalog: AgentCatalogSnapshot | None = None,
        expected_workspace_identity: WorkspaceIdentity | None = None,
    ) -> tuple[dict[str, object], dict[str, object]]: ...

    def read_run(self, run_id: str) -> dict[str, object]: ...

    def workspace_for_run(self, run_id: str) -> WorkspaceIdentity: ...

    def workspace_for_session(self, session_id: str) -> WorkspaceIdentity: ...

    def session_is_projectless(self, session_id: str) -> bool: ...

    def read_model_profile(self, run_id: str) -> ModelProfileSnapshot: ...

    def read_context_details(self, run_id: str) -> ContextDetails: ...

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

    def model_config(self, model_id: str) -> ModelConfig | None: ...

    def model_for(self, model_id: str) -> ModelClient | None: ...

    def freeze_model_config(self, run_id: str, config: ModelConfig) -> None: ...

    def discard_model_config(self, run_id: str) -> None: ...

    def extension_snapshot(self) -> dict[str, object]: ...

    def validate_input_selections(self, snapshots: list[InputSnapshot]) -> None: ...

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
        self.release_after_commit()

    def release_after_commit(self) -> None:
        """Release accepted internal work without fabricating an RPC response."""
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
        agent_catalog: AgentCatalog | None = None,
    ) -> None:
        self._typed_repository = (
            TypedRuntimeRepository(database) if database is not None else None
        )
        self._agent_catalog = agent_catalog
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

    def read_model_profile(self, run_id: str) -> ModelProfileSnapshot:
        if self._store is None:
            raise RuntimeError("RunApplication store is not configured")
        return self._store.read_model_profile(run_id)

    def input_reference_ids(self, run_id: str) -> list[str]:
        if self._store is None:
            raise RuntimeError("store is not configured")
        return [value.reference.id for value in InputContextRepository(self._store.database).for_run(run_id)]

    def select_automation_model(self, model: AutomationModel) -> AutomationModel:
        _store, _runtime, environment, _scan = self._start_dependencies()
        request = RunStartRequestDto(sessionId="00000000-0000-0000-0000-000000000000", userInput="scheduled task",
            modelId=model.model_id, reasoningSelection=model.reasoning_selection)
        model_id, profile, _config = self._select_model(request, environment=environment)
        return AutomationModel(model_id=model_id, reasoning_selection=profile.reasoning_selection)

    def start(self, request: RunStartRequestDto, *, automation_occurrence_id: str | None = None, regenerate_source_run_id: str | None = None, agent_source_run_id: str | None = None, channel_message_id: str | None = None) -> RunStartOutcome:
        store, runtime, environment, scan_text = self._start_dependencies()
        if channel_message_id is not None:
            from eidos_runtime.persistence.channels import ChannelRepository
            snapshot = ChannelRepository(store.database).inbound(channel_message_id).connection
            if (request.approval_mode != snapshot.config.approval_mode
                    or request.full_access_confirmation != snapshot.full_access_confirmation):
                raise ApplicationError("INVALID_PARAMS", "渠道任务的审批模式必须与已确认的渠道配置一致。")
        if not environment.model_is_configured():
            raise ApplicationError("INVALID_STATE", "no model is configured")
        try:
            user_input = scan_text(request.user_input)
        except SensitiveContentDenied as error:
            raise ApplicationError("SENSITIVE_CONTENT_REJECTED", str(error)) from error
        except SensitiveScanError as error:
            raise ApplicationError("SENSITIVE_SCAN_FAILED", str(error)) from error

        from eidos_runtime.persistence.collaboration import CollaborationRepository
        if CollaborationRepository(store.database).is_child(request.session_id):
            raise ApplicationError("INVALID_STATE", "Delegate follow-up work through the parent agent.")
        session = store.read_session(request.session_id)
        if session is None:
            raise ApplicationError("RESOURCE_NOT_FOUND", "session not found")

        model_id, model_profile, model_config = self._select_model(
            request,
            environment=environment,
        )
        try:
            input_snapshots = [InputContextRepository(store.database).read(identifier) for identifier in dict.fromkeys(request.references)]
        except ValueError as error:
            raise ApplicationError("RESOURCE_NOT_FOUND", "引用已失效。") from error
        if sum(len(value.image or "") for value in input_snapshots) > 16 * 1024 * 1024:
            raise ApplicationError("INVALID_PARAMS", "图片总量过大。")
        if sum(len(value.text) for value in input_snapshots) > 256 * 1024:
            raise ApplicationError("INVALID_PARAMS", "引用内容总量过大。")
        if any(value.image for value in input_snapshots) and not model_profile.supports_images:
            raise ApplicationError("INVALID_PARAMS", "当前模型不支持图片输入。")
        if request.plan_id is not None:
            from eidos_runtime.persistence.planning import PlanningRepository
            try:
                plan_repository = PlanningRepository(store.database)
                document = plan_repository.read(request.plan_id)
                if document.session_id != request.session_id:
                    raise ValueError('plan_not_found')
                plan_repository.materialize(document)
            except (ValueError, OSError) as error:
                raise ApplicationError("INVALID_STATE", str(error)) from error
        extension_snapshot = environment.extension_snapshot()
        try:
            workspace = self._admit_session_workspace(
                request.session_id, touch_last_used=False
            )
            if workspace is None:
                workspace = store.workspace_for_session(request.session_id)
        except ResourceNotFoundError as error:
            raise ApplicationError("RESOURCE_NOT_FOUND", str(error)) from error
        except WorktreeError as error:
            raise ApplicationError(
                _run_worktree_error_code(error), str(error)
            ) from error
        except WorkspaceIdentityChangedError as error:
            raise ApplicationError("WORKSPACE_IDENTITY_CHANGED", str(error)) from error
        except WorkspaceBoundaryError as error:
            raise ApplicationError("WORKSPACE_BOUNDARY_VIOLATION", str(error)) from error
        except StorageError as error:
            raise ApplicationError("INVALID_STATE", str(error)) from error
        from eidos_runtime.extensions.hook_sources import HookSourceCatalog
        configured_hooks = HookSourceCatalog(store).bindings(workspace.path)
        if configured_hooks:
            extension_snapshot = dict(extension_snapshot or {})
            extension_snapshot["hooks"] = [*extension_snapshot.get("hooks", []),
                *(binding.to_wire_dict() for binding in configured_hooks)]
        operation_request: dict[str, object] = {
            **({"regenerateSourceRunId": regenerate_source_run_id} if regenerate_source_run_id is not None else {}),
            "sessionId": request.session_id,
            "userInput": user_input,
            "workMode": request.work_mode,
            "planId": request.plan_id,
            "planRevision": request.plan_revision,
            **({"references": [value.reference.id for value in input_snapshots]} if input_snapshots else {}),
            "modelId": model_id,
            "reasoningSelection": model_profile.reasoning_selection,
            "extensionSnapshot": extension_snapshot,
            **({"approvalMode": request.approval_mode} if request.approval_mode != "manual" else {}),
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

        agent_snapshot = AgentCatalogSnapshot()
        if self._agent_catalog is not None:
            previous = AgentSnapshotRepository(store.database).read(agent_source_run_id) if agent_source_run_id else None
            retained = set(self.input_reference_ids(agent_source_run_id)) if agent_source_run_id else None
            try:
                agent_snapshot = self._agent_catalog.freeze(input_snapshots, previous=previous, retained_ids=retained)
            except ValueError as error:
                raise ApplicationError("INVALID_STATE", str(error)) from error
        if input_snapshots:
            environment.validate_input_selections([value for value in input_snapshots if value.reference.kind != "agent"])

        try:
            if self._session_repository is not None:
                projection = self._session_repository.read_session_projection(
                    request.session_id
                )
                if projection is None:
                    raise ResourceNotFoundError("session not found")
                workspace_lock = (
                    self._session_lifecycle.hold_workspace(
                        projection.session.workspace_root
                    )
                    if projection.worktree is None
                    else nullcontext()
                )
            else:
                workspace_lock = self._session_lifecycle.hold_workspace(
                    store.workspace_for_session(request.session_id).path
                )
            with workspace_lock:
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
                    if self._repository_runtime is not None and not store.session_is_projectless(
                        request.session_id
                    ):
                        self._repository_runtime.activate_workspace(workspace.path)
                    needs_title = "title" not in current_session
                    if automation_occurrence_id is not None:
                        from eidos_runtime.persistence.automations import AutomationRepository
                        from eidos_runtime.db.repositories.runs import RunRepository
                        repository = AutomationRepository(store.database)
                        created, _user_item = repository.accept(automation_occurrence_id,
                            lambda connection, _origin: RunRepository(store.database).create_run(
                                request.session_id, user_input, queued=True,
                                references=[value.reference for value in input_snapshots],
                                model_id=model_id, model_profile=model_profile, approval_mode=request.approval_mode,
                                work_mode=request.work_mode, extension_snapshot=extension_snapshot, agent_catalog=agent_snapshot,
                                expected_workspace_identity=expected_workspace_identity,
                                transaction_connection=connection,
                            ), request.session_id)
                    elif channel_message_id is not None:
                        from eidos_runtime.persistence.channels import ChannelRepository
                        from eidos_runtime.db.repositories.runs import RunRepository
                        channel_run_id = ChannelRepository(store.database).accept(channel_message_id, request.session_id,
                            lambda connection: str(RunRepository(store.database).create_run(
                                request.session_id, user_input, queued=True,
                                channel_context=True,
                                model_id=model_id, model_profile=model_profile, approval_mode=request.approval_mode,
                                extension_snapshot=extension_snapshot, agent_catalog=agent_snapshot,
                                expected_workspace_identity=expected_workspace_identity, transaction_connection=connection,
                            )[0]["id"]))
                        created = store.read_run(channel_run_id)
                    elif regenerate_source_run_id is not None:
                        from eidos_runtime.db.repositories.runs import RunRepository
                        created, _user_item = RunRepository(store.database).create_run(
                            request.session_id, user_input, queued=True,
                            references=[value.reference for value in input_snapshots], operation_id=request.operation_id,
                            model_id=model_id, model_profile=model_profile, approval_mode=request.approval_mode,
                            work_mode=request.work_mode, extension_snapshot=extension_snapshot, agent_catalog=agent_snapshot,
                            expected_workspace_identity=expected_workspace_identity,
                            regenerate_source_run_id=regenerate_source_run_id,
                        )
                    else:
                        created, _user_item = store.enqueue_run(
                            request.session_id, user_input,
                            references=[value.reference for value in input_snapshots],
                            operation_id=request.operation_id, session_title=None,
                            model_id=model_id, model_profile=model_profile, approval_mode=request.approval_mode,
                            work_mode=request.work_mode, plan_id=request.plan_id, plan_revision=request.plan_revision,
                            extension_snapshot=extension_snapshot, agent_catalog=agent_snapshot,
                            expected_workspace_identity=expected_workspace_identity,
                        )
        except AutomationError:
            raise
        except ValueError as error:
            raise ApplicationError("INVALID_STATE", str(error)) from error
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
                    user_input=user_input or ", ".join(value.reference.label for value in input_snapshots),
                    model_id=model_id,
                )
                if needs_title and automation_occurrence_id is None
                else None
            ),
        )

    def _admit_session_workspace(
        self, session_id: str, *, touch_last_used: bool = True
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
        managed = manager.execution_identity(projection.worktree.worktree_id)
        if self._store is None:
            raise ApplicationError("INTERNAL_ERROR", "Run store is unavailable")
        identity = self._store.workspace_for_session(session_id)
        if not identity.path.is_relative_to(managed.path):
            raise WorkspaceBoundaryError("session workspace is outside its Worktree")
        from eidos_runtime.workspace.reader import WorkspaceReader, WorkspacePathError
        try:
            for root in (identity, *identity.additional_roots):
                with WorkspaceReader(root):
                    pass
        except WorkspacePathError as error:
            raise WorkspaceIdentityChangedError(error.code) from error
        if touch_last_used:
            manager.touch_last_used(projection.worktree.worktree_id)
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
            "waiting_input", "waiting_agents",
            "finalizing",
            "canceled",
            "interrupted",
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
            if current.get("status") not in {"canceled", "interrupted"}:
                raise ApplicationError("INVALID_STATE", "run cannot be canceled")
        except RunCancelTimeout as error:
            raise ApplicationError("RUN_CANCEL_TIMEOUT", str(error)) from error
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
            usage = _context_usage_snapshot(store.read_context_details(request.run_id))
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
        environment: RunStartEnvironmentPort,
    ) -> tuple[str, ModelProfileSnapshot, ModelConfig | None]:
        model_id = request.model_id
        config = environment.model_config(model_id)
        try:
            selected_reasoning = MODEL_CATALOG.reasoning_selection(
                model_id, request.reasoning_selection, config=config
            )
        except ModelConfigError as error:
            raise ApplicationError(
                "MODEL_NOT_AVAILABLE", "model is unavailable"
            ) from error
        except ModelReasoningSelectionError as error:
            raise ApplicationError(
                "INVALID_PARAMS", "reasoning selection is unsupported for this model"
            ) from error
        run_model = environment.model_for(model_id)
        if run_model is not None:
            profile = getattr(run_model, "profile_snapshot", None)
            base_profile = profile or default_profile_snapshot(model_id)
            return (
                model_id,
                base_profile.model_copy(update={
                    "reasoning_selection": selected_reasoning
                }),
                None,
            )
        if config is not None:
            return (
                model_id,
                MODEL_CATALOG.profile(model_id, config=config).snapshot(
                    config, reasoning_selection=selected_reasoning
                ),
                config,
            )
        raise ApplicationError("MODEL_NOT_AVAILABLE", "model is unavailable")

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
    details: ContextDetails,
) -> ContextUsageSnapshot | None:
    if details.active_tokens is None:
        return None
    return ContextUsageSnapshot(
        active_tokens=details.active_tokens, context_window_tokens=details.context_window_tokens,
        percent_used=round(details.active_tokens / details.context_window_tokens * 100, 1),
        source="provider" if details.measurement == "provider" else "estimated", updated_at=details.updated_at,
    )


__all__ = [
    "RunApplication",
    "RunRuntimePort",
    "RunStartEnvironmentPort",
    "RunStartOutcome",
    "RunStorePort",
]
