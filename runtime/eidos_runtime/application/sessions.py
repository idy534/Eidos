from __future__ import annotations

import hashlib
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime
import logging
from pathlib import Path
import unicodedata
import uuid
import threading
from typing import Protocol, TypeVar

from pydantic import ValidationError

from eidos_runtime.application.errors import (
    ApplicationError,
    ApplicationInvalidParamsError,
)
from eidos_runtime.application.session_lifecycle import SessionLifecycleCoordinator
from eidos_runtime.application.git_workflow import (
    GitFetchPlan,
    GitMergePlan,
    GitMutationPlan,
    GitPullPlan,
    GitPushPlan,
    GitRebasePlan,
    GitWorkflowApplication,
)
from eidos_runtime.db.database import CommittedMutation
from eidos_runtime.db.errors import (
    InvalidCursorError,
    InvalidRunStateError,
    OperationConflictError,
    OperationFailedError,
    OperationInProgressError,
    ResourceNotFoundError,
    SessionActiveError,
    StorageError,
    WorkspaceBoundaryError,
)
from eidos_runtime.domain.project import Project
from eidos_runtime.domain.session import Session, SessionExecutionMode
from eidos_runtime.domain.worktree import (
    Worktree,
    WorktreeLifecycleOperation,
    WorktreeLifecycleScope,
    WorktreeLifecycleState,
    WorktreeOwnership,
    WorktreeState,
)
from eidos_runtime.git.errors import WorktreeError
from eidos_runtime.git.models import (
    GitRepositoryContext,
    GitRepositoryDiscovery,
    GitSourceSnapshot,
    GitWorkingTreePatch,
    ProjectResolution,
)
from eidos_runtime.git.status import DiffScope, GitDiffSnapshot, GitStatusSnapshot
from eidos_runtime.protocol.methods import (
    EventListRequestDto,
    EventListResponseDto,
    MethodResultDto,
    SessionCreateBranchRequestDto,
    SessionCreateBranchResponseDto,
    SessionCreateRequestDto,
    SessionCreateResponseDto,
    SessionDeleteRequestDto,
    SessionDeleteResponseDto,
    SessionHandoffRequestDto,
    SessionHandoffResponseDto,
    SessionRestoreWorktreeRequestDto,
    SessionRestoreWorktreeResponseDto,
    SessionGitDiffRequestDto,
    SessionGitDiffResponseDto,
    SessionGitCommitRequestDto,
    SessionGitCommitResponseDto,
    SessionGitDiscardRequestDto,
    SessionGitDiscardResponseDto,
    SessionGitFetchRequestDto,
    SessionGitFetchResponseDto,
    SessionGitMergeAbortRequestDto,
    SessionGitMergeAbortResponseDto,
    SessionGitMergeRequestDto,
    SessionGitMergeResponseDto,
    SessionGitPullRequestDto,
    SessionGitPullResponseDto,
    SessionGitPushRequestDto,
    SessionGitPushResponseDto,
    SessionGitRemoteStatusRequestDto,
    SessionGitRemoteStatusResponseDto,
    SessionGitRebaseAbortRequestDto,
    SessionGitRebaseAbortResponseDto,
    SessionGitRebaseContinueRequestDto,
    SessionGitRebaseContinueResponseDto,
    SessionGitRebaseRequestDto,
    SessionGitRebaseResponseDto,
    SessionGitStageRequestDto,
    SessionGitStageResponseDto,
    SessionGitStatusRequestDto,
    SessionGitStatusResponseDto,
    SessionGitUnstageRequestDto,
    SessionGitUnstageResponseDto,
    SessionListRequestDto,
    SessionListResponseDto,
    SessionReadRequestDto,
    SessionReadResponseDto,
    SessionRenameRequestDto,
    SessionRenameResponseDto,
    GitContextRequestDto,
    GitContextResponseDto,
)
from eidos_runtime.domain.session import (
    DeletedSession,
    SessionPage,
    SessionProjection,
    SessionProjectionPage,
    SessionWorktreeProjection,
)
from eidos_runtime.persistence.mappers.session import (
    deleted_session_to_legacy_dict,
    session_from_legacy_dict,
    session_to_legacy_dict,
)
from eidos_runtime.persistence.worktree_lifecycle import WorktreeLifecycleRepository
from eidos_runtime.persistence.session_handoff import SessionHandoffRepository
from eidos_runtime.domain.handoff import (
    HandoffPlan,
    SessionHandoffOperation,
    SessionHandoffScope,
    SessionHandoffState,
)
from eidos_runtime.protocol.schemas import (
    SessionDto,
    SessionProjectDto,
    SessionWorktreeDto,
)
from eidos_runtime.sandbox.sensitive import (
    SensitiveContentDenied,
    SensitiveScanError,
)


MAX_SESSION_TITLE_BYTES = 120
ResultT = TypeVar("ResultT", bound=MethodResultDto)
GitPlanT = TypeVar("GitPlanT", GitMutationPlan, GitMergePlan, GitRebasePlan)


class TypedSessionRepositoryPort(Protocol):
    """Typed Session reads/writes consumed by the application layer."""

    def create_session(
        self,
        workspace_root: str,
        *,
        worktree_id: str | None = None,
        execution_mode: SessionExecutionMode = SessionExecutionMode.LOCAL,
        project_id: str | None = None,
        operation_id: str | None = None,
        session_id: str | None = None,
    ) -> CommittedMutation[Session]: ...

    def list_sessions(
        self, *, limit: int, cursor: str | None
    ) -> SessionPage: ...

    def list_session_projections(
        self, *, limit: int, cursor: str | None
    ) -> SessionProjectionPage: ...

    def read_session(self, session_id: str) -> Session | None: ...

    def read_session_projection(self, session_id: str) -> SessionProjection | None: ...

    def rename_session(
        self,
        session_id: str,
        title: str,
        *,
        operation_id: str | None = None,
    ) -> CommittedMutation[Session]: ...

    def delete_session(
        self, session_id: str, *, operation_id: str | None = None
    ) -> CommittedMutation[DeletedSession]: ...

    def assert_session_deletable(self, session_id: str) -> None: ...

    def assert_session_idle(self, session_id: str) -> None: ...

    def update_execution_binding(
        self,
        session_id: str,
        *,
        execution_mode: SessionExecutionMode,
        worktree_id: str | None,
        associated_worktree_id: str | None,
    ) -> CommittedMutation[Session]: ...

    def session_handoff_repository(self) -> SessionHandoffRepository: ...


class SessionStorePort(Protocol):
    """Compatibility store for aggregate reads not yet represented as domains.

    Session create/list/read/rename/delete use ``TypedSessionRepositoryPort``.
    The aggregate snapshot and historical event feeds retain their established
    wire records until those records have their own domain models.
    """

    def typed_runtime_repository(self) -> TypedSessionRepositoryPort: ...

    def read_session_snapshot(
        self,
        session_id: str,
        *,
        item_limit: int,
        before_item_id: str | None,
    ) -> dict[str, object]: ...

    def list_events(
        self, session_id: str, *, after_event_id: int, limit: int
    ) -> dict[str, object]: ...

    def operation_result(
        self, operation_id: str, scope: str, request: dict[str, object]
    ) -> object | None: ...

    def prepare_operation(
        self, operation_id: str, scope: str, request: dict[str, object]
    ) -> object | None: ...

    def complete_operation(
        self,
        operation_id: str,
        scope: str,
        request: dict[str, object],
        result: dict[str, object],
    ) -> dict[str, object]: ...

    def fail_operation(
        self,
        operation_id: str,
        scope: str,
        request: dict[str, object],
        *,
        error_code: str,
        side_effects_may_exist: bool,
    ) -> None: ...

    def accept_async_operation(
        self,
        *,
        request_id: str | None,
        operation_id: str,
        scope: str,
        request: dict[str, object],
    ): ...

    def prepare_deferred_external_operation(
        self,
        *,
        request_id: str | None,
        operation_id: str,
        scope: str,
        request: dict[str, object],
    ): ...

    def start_async_operation(self, operation_id: str): ...

    def complete_async_operation(
        self, operation_id: str, result: dict[str, object]
    ): ...

    def fail_async_operation(self, operation_id: str, error_code: str): ...

    def cancel_async_operation(self, operation_id: str): ...

    def record_operation_result(
        self,
        operation_id: str,
        scope: str,
        request: dict[str, object],
        result: dict[str, object],
    ) -> dict[str, object]: ...

    def session_handoff_repository(self) -> SessionHandoffRepository: ...


class RepositoryWorkspaceRuntimePort(Protocol):
    def activate_workspace(self, root: Path) -> object: ...


class ManagedWorktreePort(Protocol):
    """Application port for Session-owned Worktree provisioning."""

    def resolve_project(
        self, workspace_seed: Path | str
    ) -> ProjectResolution: ...

    def discover(self, repository_seed: Path | str) -> GitRepositoryDiscovery: ...

    def create(self, repository_root: Path | str) -> Worktree: ...

    def source_snapshot(
        self,
        repository_root: Path,
        *,
        include_local_changes: bool,
    ) -> GitSourceSnapshot: ...

    def prepare_create(
        self, repository_root: Path | str, base_ref: str | None = None
    ) -> Worktree: ...

    def local_branches(self, repository_root: Path) -> tuple[str, ...]: ...

    def head(self, repository_root: Path) -> str: ...

    def current_branch(self, repository_root: Path) -> str | None: ...

    def create_prepared(
        self,
        plan: Worktree,
        *,
        compensate_on_failure: bool = True,
        include_local_changes: bool = False,
        expected_source_head: str | None = None,
        expected_source_fingerprint: str | None = None,
    ) -> Worktree: ...

    def prepared_from_lifecycle(
        self, operation: WorktreeLifecycleOperation
    ) -> Worktree: ...

    def project(self, project_id: str) -> Project: ...

    def status(self, worktree_id: str) -> GitStatusSnapshot: ...

    def local_status(self, repository_root: Path) -> GitStatusSnapshot: ...

    def diff(
        self,
        worktree_id: str,
        *,
        scope: DiffScope = DiffScope.HEAD,
        path: str | None = None,
    ) -> GitDiffSnapshot: ...

    def local_diff(
        self,
        repository_root: Path,
        *,
        scope: DiffScope = DiffScope.HEAD,
        path: str | None = None,
    ) -> GitDiffSnapshot: ...

    def delete(self, worktree_id: str) -> Worktree: ...

    def rollback_create(self, worktree_id: str) -> Worktree: ...

    def prepare_branch_attachment(
        self, worktree_id: str, branch: str
    ) -> Worktree: ...

    def attach_branch_git(
        self,
        worktree_id: str,
        branch: str,
        *,
        expected_head: str | None = None,
    ) -> Worktree: ...

    def persist_branch(
        self,
        worktree_id: str,
        branch: str,
        *,
        expected_head: str | None = None,
    ) -> Worktree: ...

    @property
    def lifecycle(self) -> WorktreeLifecycleRepository: ...

    def read_worktree(self, worktree_id: str) -> Worktree: ...

    def capture_worktree_changes(self, root: Path) -> GitWorkingTreePatch: ...

    def apply_worktree_changes(
        self, root: Path, changes: GitWorkingTreePatch
    ) -> None: ...

    def switch_repository_branch(self, root: Path, branch: str) -> None: ...

    def switch_repository_detached(self, root: Path, commit: str) -> None: ...

    def detach_worktree_for_handoff(
        self, worktree_id: str, *, expected_head: str
    ) -> Worktree: ...

    def move_worktree_to_head(
        self,
        worktree_id: str,
        *,
        expected_current_head: str,
        target_head: str,
    ) -> Worktree: ...

    def clean_worktree_after_handoff(
        self, worktree_id: str, *, expected_head: str
    ) -> Worktree: ...

    def release_user_branch_after_handoff(
        self,
        worktree_id: str,
        *,
        expected_branch: str,
        expected_head: str,
        target_root: Path,
        expected_target_fingerprint: str | None = None,
    ) -> Worktree: ...

    def touch_last_used(self, worktree_id: str) -> Worktree: ...


class WorktreeRetentionPort(Protocol):
    def reconcile(self) -> object: ...

    def restore_worktree(
        self, worktree_id: str, *, operation_id: str | None = None
    ) -> Worktree: ...

    def delete_snapshots_for_worktree(self, worktree_id: str) -> None: ...

    def has_ready_snapshot(self, worktree_id: str) -> bool: ...

    def latest_ready_snapshot_id(self, worktree_id: str) -> str | None: ...


@dataclass(frozen=True)
class DeferredGitFetch:
    async_operation_id: str
    operation_id: str
    request: dict[str, object]
    plan: GitFetchPlan
    _application: "SessionApplication" = field(repr=False, compare=False)

    def run(self, cancel: threading.Event) -> SessionGitFetchResponseDto:
        return self._application._run_git_fetch(self, cancel)

    def cancel_before_start(self) -> None:
        self._application._cancel_git_external_before_start(self)


@dataclass(frozen=True)
class DeferredGitPull:
    async_operation_id: str
    operation_id: str
    request: dict[str, object]
    plan: GitPullPlan
    _application: "SessionApplication" = field(repr=False, compare=False)

    def run(self, cancel: threading.Event) -> SessionGitPullResponseDto:
        return self._application._run_git_pull(self, cancel)

    def cancel_before_start(self) -> None:
        self._application._cancel_git_external_before_start(self)


@dataclass(frozen=True)
class DeferredGitPush:
    async_operation_id: str
    operation_id: str
    request: dict[str, object]
    plan: GitPushPlan
    _application: "SessionApplication" = field(repr=False, compare=False)

    def run(self, cancel: threading.Event) -> SessionGitPushResponseDto:
        return self._application._run_git_push(self, cancel)

    def cancel_before_start(self) -> None:
        self._application._cancel_git_external_before_start(self)


def clean_session_title(value: str) -> str:
    """Apply the established title canonicalization before durable storage."""

    title = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in value
    )
    title = " ".join(title.split()).strip("\"'`“”‘’ ")[:60]
    return title.encode("utf-8")[:MAX_SESSION_TITLE_BYTES].decode(
        "utf-8", errors="ignore"
    ).strip()


class SessionApplication:
    """Owns top-level Session and Session-event use cases.

    Typed Session writes/reads flow through the public repository seam.  The
    Store remains the single transactional authority; aggregate snapshots and
    historical event feeds keep their established compatibility records until
    they receive dedicated domain models.
    """

    def __init__(
        self,
        store: SessionStorePort,
        *,
        scan_text: Callable[[str], str],
        worktree_manager: ManagedWorktreePort | None = None,
        lifecycle: SessionLifecycleCoordinator | None = None,
        retention: WorktreeRetentionPort | None = None,
        repository_runtime: RepositoryWorkspaceRuntimePort | None = None,
    ) -> None:
        self._store = store
        self._repository: TypedSessionRepositoryPort = (
            store.typed_runtime_repository()
        )
        self._scan_text = scan_text
        self._worktree_manager = worktree_manager
        self._git_workflow = (
            GitWorkflowApplication(self._repository, worktree_manager)
            if worktree_manager is not None
            else None
        )
        self._retention = retention
        self._repository_runtime = repository_runtime
        self._lifecycle = lifecycle or SessionLifecycleCoordinator()
        self._logger = logging.getLogger(__name__)

    def create(self, request: SessionCreateRequestDto) -> SessionCreateResponseDto:
        execution_mode = SessionExecutionMode(request.execution_mode)
        resolution: ProjectResolution | None = None
        if self._worktree_manager is not None:
            operation_guard = (
                self._lifecycle.hold_operation(
                    "session/create", request.operation_id
                )
                if request.operation_id is not None
                else nullcontext()
            )
            with operation_guard:
                resolution = self._resolve_project(request.workspace_root)
                if execution_mode is SessionExecutionMode.WORKTREE:
                    if resolution.git is None:
                        raise ApplicationError(
                            "WORKTREE_REQUIRES_GIT",
                            "worktree execution requires a Git repository",
                        )
                    return self._activate_created_session(
                        self._create_managed(request, resolution)
                    )
        try:
            mutation = self._repository.create_session(
                request.workspace_root,
                execution_mode=execution_mode,
                project_id=resolution.project.id if resolution is not None else None,
                operation_id=request.operation_id,
            )
        except WorkspaceBoundaryError as error:
            raise ApplicationError(
                "WORKSPACE_BOUNDARY_VIOLATION", str(error)
            ) from error
        except OperationConflictError as error:
            raise ApplicationError("OPERATION_ID_REUSED", str(error)) from error
        except OperationInProgressError as error:
            raise ApplicationError("OPERATION_IN_PROGRESS", str(error)) from error
        return self._activate_created_session(_result(
            SessionCreateResponseDto,
            self._project_session(self._projection_for_session(mutation.value.id)),
        ))

    def _activate_created_session(
        self, result: SessionCreateResponseDto
    ) -> SessionCreateResponseDto:
        runtime = self._repository_runtime
        if runtime is None:
            return result
        root = (
            Path(result.worktree.worktree_root)
            if result.worktree is not None
            else Path(result.workspace_root)
        )
        runtime.activate_workspace(root)
        return result

    def _resolve_project(self, workspace_root: str) -> ProjectResolution:
        manager = self._worktree_manager
        assert manager is not None
        try:
            return manager.resolve_project(workspace_root)
        except WorktreeError as error:
            raise ApplicationError(_workspace_resolution_error_code(error), str(error)) from error

    def create_branch(
        self, request: SessionCreateBranchRequestDto
    ) -> SessionCreateBranchResponseDto:
        manager = self._worktree_manager
        if manager is None:
            raise ApplicationError(
                "INTERNAL_ERROR", "managed Session has no Worktree application boundary"
            )
        operation_request = {
            "sessionId": request.session_id,
            "branch": request.branch,
        }
        operation_guard = (
            self._lifecycle.hold_operation(
                "session/createBranch", request.operation_id
            )
            if request.operation_id is not None
            else nullcontext()
        )
        with operation_guard:
            if request.operation_id is not None:
                try:
                    replay = self._store.operation_result(
                        request.operation_id,
                        "session/createBranch",
                        operation_request,
                    )
                except OperationConflictError as error:
                    raise ApplicationError("OPERATION_ID_REUSED", str(error)) from error
                except OperationInProgressError as error:
                    raise ApplicationError("OPERATION_IN_PROGRESS", str(error)) from error
                if replay is not None:
                    return _result(SessionCreateBranchResponseDto, replay)

            session = self._repository.read_session(request.session_id)
            if session is None:
                raise ApplicationError("RESOURCE_NOT_FOUND", "session not found")
            if session.execution_mode is not SessionExecutionMode.WORKTREE:
                raise ApplicationError(
                    "WORKTREE_REQUIRED", "Create Branch requires a Worktree Session"
                )
            if session.worktree_id is None:
                raise ApplicationError(
                    "WORKTREE_REQUIRED", "Create Branch requires a managed Worktree"
                )

            lifecycle = manager.lifecycle
            operation_id = request.operation_id or (
                f"worktree-attach-branch-{uuid.uuid4().hex}"
            )
            lifecycle_operation = lifecycle.read(
                WorktreeLifecycleScope.ATTACH_BRANCH,
                operation_id,
            )
            try:
                if lifecycle_operation is None:
                    prepared = manager.prepare_branch_attachment(
                        session.worktree_id, request.branch
                    )
                    expected_head = manager.head(Path(prepared.worktree_root))
                    now = datetime.now(UTC).replace(microsecond=0)
                    lifecycle_operation = lifecycle.prepare(
                        WorktreeLifecycleOperation(
                            scope=WorktreeLifecycleScope.ATTACH_BRANCH,
                            operation_id=operation_id,
                            state=WorktreeLifecycleState.PREPARED,
                            project_id=prepared.project_id,
                            repository_root=manager.project(
                                prepared.project_id
                            ).workspace_root,
                            worktree_id=prepared.id,
                            worktree_root=prepared.worktree_root,
                            base_ref=prepared.base_ref,
                            branch=request.branch,
                            base_commit=prepared.base_commit,
                            expected_head=expected_head,
                            session_id=request.session_id,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                if (
                    lifecycle_operation.session_id != request.session_id
                    or lifecycle_operation.worktree_id != session.worktree_id
                    or lifecycle_operation.branch != request.branch
                ):
                    raise ApplicationError(
                        "OPERATION_ID_REUSED",
                        "branch operation does not match the Session Worktree",
                    )
                if lifecycle_operation.state is WorktreeLifecycleState.CLEANUP_REQUIRED:
                    raise ApplicationError(
                        "WORKTREE_RECOVERY_REQUIRED",
                        lifecycle_operation.error_code
                        or "branch attachment recovery is required",
                    )
                if lifecycle_operation.state is WorktreeLifecycleState.PREPARED:
                    if lifecycle_operation.expected_head is None:
                        raise WorktreeError("worktree_lifecycle_invalid")
                    manager.attach_branch_git(
                        session.worktree_id,
                        request.branch,
                        expected_head=lifecycle_operation.expected_head,
                    )
                    lifecycle_operation = lifecycle.update_state(
                        WorktreeLifecycleScope.ATTACH_BRANCH,
                        operation_id,
                        WorktreeLifecycleState.BRANCH_ATTACHED,
                    )
                worktree: Worktree | None = None
                if lifecycle_operation.state is WorktreeLifecycleState.BRANCH_ATTACHED:
                    if lifecycle_operation.expected_head is None:
                        raise WorktreeError("worktree_lifecycle_invalid")
                    worktree = manager.persist_branch(
                        session.worktree_id,
                        request.branch,
                        expected_head=lifecycle_operation.expected_head,
                    )
                    lifecycle_operation = lifecycle.update_state(
                        WorktreeLifecycleScope.ATTACH_BRANCH,
                        operation_id,
                        WorktreeLifecycleState.COMPLETED,
                    )
                if worktree is None:
                    if lifecycle_operation.expected_head is None:
                        raise WorktreeError("worktree_lifecycle_invalid")
                    worktree = manager.persist_branch(
                        session.worktree_id,
                        request.branch,
                        expected_head=lifecycle_operation.expected_head,
                    )
                head = manager.head(Path(worktree.worktree_root))
                result = _result(
                    SessionCreateBranchResponseDto,
                    {
                        "sessionId": request.session_id,
                        "worktreeId": worktree.id,
                        "branch": worktree.branch,
                        "head": head,
                    },
                )
                if request.operation_id is None:
                    return result
                try:
                    recorded = self._store.record_operation_result(
                        request.operation_id,
                        "session/createBranch",
                        operation_request,
                        result.to_json_value(),
                    )
                except OperationConflictError as error:
                    raise ApplicationError("OPERATION_ID_REUSED", str(error)) from error
                except OperationInProgressError as error:
                    raise ApplicationError("OPERATION_IN_PROGRESS", str(error)) from error
                return _result(SessionCreateBranchResponseDto, recorded)
            except ApplicationError:
                raise
            except WorktreeError as error:
                self._mark_branch_attach_cleanup(
                    manager,
                    lifecycle_operation,
                    operation_id,
                    error.code,
                )
                raise ApplicationError(_branch_error_code(error), str(error)) from error
            except StorageError as error:
                self._mark_branch_attach_cleanup(
                    manager,
                    lifecycle_operation,
                    operation_id,
                    "worktree_branch_persistence_failed",
                )
                raise ApplicationError("WORKTREE_RECOVERY_REQUIRED", str(error)) from error

    @staticmethod
    def _mark_branch_attach_cleanup(
        manager: ManagedWorktreePort,
        operation: WorktreeLifecycleOperation | None,
        operation_id: str,
        error_code: str,
    ) -> None:
        if operation is None or operation.state in {
            WorktreeLifecycleState.COMPLETED,
            WorktreeLifecycleState.CLEANUP_REQUIRED,
        }:
            return
        try:
            manager.lifecycle.update_state(
                WorktreeLifecycleScope.ATTACH_BRANCH,
                operation_id,
                WorktreeLifecycleState.CLEANUP_REQUIRED,
                error_code=error_code,
            )
        except Exception:
            # The operation remains durable even when this final state write
            # cannot be completed. Startup recovery will inspect it again.
            return

    def _create_managed(
        self,
        request: SessionCreateRequestDto,
        resolution: ProjectResolution | None = None,
    ) -> SessionCreateResponseDto:
        manager = self._worktree_manager
        assert manager is not None
        try:
            discovery = resolution.git if resolution is not None else manager.discover(
                Path(request.workspace_root)
            )
            if discovery is None:
                raise WorktreeError("not_a_git_repository")
        except WorkspaceBoundaryError as error:
            raise ApplicationError(
                "WORKSPACE_BOUNDARY_VIOLATION", str(error)
            ) from error
        except WorktreeError as error:
            raise ApplicationError(_worktree_error_code(error), str(error)) from error

        operation_request: dict[str, object] = {
            "workspaceRoot": discovery.repository_root,
            "executionMode": SessionExecutionMode.WORKTREE.value,
            "includeLocalChanges": request.include_local_changes,
        }
        if request.base_ref is not None:
            operation_request["baseRef"] = request.base_ref
        if request.operation_id is not None:
            try:
                replay = self._store.operation_result(
                    request.operation_id, "session/create", operation_request
                )
            except OperationConflictError as error:
                raise ApplicationError("OPERATION_ID_REUSED", str(error)) from error
            except OperationInProgressError as error:
                raise ApplicationError("OPERATION_IN_PROGRESS", str(error)) from error
            if replay is not None:
                replayed_session = session_from_legacy_dict(replay)
                return _result(
                    SessionCreateResponseDto,
                    self._project_session(
                        self._projection_for_session(replayed_session.id)
                    ),
                )

        lifecycle = manager.lifecycle
        lifecycle_operation_id = request.operation_id or (
            f"session-create-{uuid.uuid4().hex}"
        )
        lifecycle_operation = lifecycle.read(
            WorktreeLifecycleScope.SESSION_CREATE,
            lifecycle_operation_id,
        )
        worktree: Worktree | None = None
        try:
            if lifecycle_operation is None:
                plan = manager.prepare_create(
                    discovery.repository_root,
                    base_ref=request.base_ref,
                )
                source_snapshot = manager.source_snapshot(
                    Path(discovery.repository_root),
                    include_local_changes=request.include_local_changes,
                )
                if (
                    request.include_local_changes
                    and source_snapshot.head != plan.base_commit
                ):
                    raise WorktreeError("local_changes_base_mismatch")
                session_id = str(uuid.uuid4())
                now = datetime.now(UTC).replace(microsecond=0)
                lifecycle_operation = lifecycle.prepare(
                    WorktreeLifecycleOperation(
                        scope=WorktreeLifecycleScope.SESSION_CREATE,
                        operation_id=lifecycle_operation_id,
                        state=WorktreeLifecycleState.PREPARED,
                        project_id=plan.project_id,
                        repository_root=discovery.repository_root,
                        worktree_id=plan.id,
                        worktree_root=plan.worktree_root,
                        base_ref=plan.base_ref,
                        branch=plan.branch,
                        base_commit=plan.base_commit,
                        session_id=session_id,
                        include_local_changes=request.include_local_changes,
                        source_head=(
                            source_snapshot.head
                            if request.include_local_changes
                            else None
                        ),
                        source_branch=(
                            source_snapshot.branch
                            if request.include_local_changes
                            else None
                        ),
                        source_dirty=(
                            source_snapshot.status.dirty
                            if request.include_local_changes
                            else None
                        ),
                        source_fingerprint=(
                            source_snapshot.fingerprint
                            if request.include_local_changes
                            else None
                        ),
                        created_at=now,
                        updated_at=now,
                    )
                )
            elif lifecycle_operation.repository_root != discovery.repository_root:
                raise ApplicationError(
                    "OPERATION_ID_REUSED",
                    "operation id was reused for another repository",
                )
            elif lifecycle_operation.include_local_changes != request.include_local_changes:
                raise ApplicationError(
                    "OPERATION_ID_REUSED",
                    "operation id was reused with a different local-change policy",
                )
            if lifecycle_operation.state is WorktreeLifecycleState.CLEANUP_REQUIRED:
                raise ApplicationError(
                    "WORKTREE_RECOVERY_REQUIRED",
                    lifecycle_operation.error_code
                    or "worktree lifecycle recovery is required",
                )
            plan = manager.prepared_from_lifecycle(lifecycle_operation)
            worktree = manager.create_prepared(
                plan,
                compensate_on_failure=True,
                include_local_changes=lifecycle_operation.include_local_changes,
                expected_source_head=lifecycle_operation.source_head,
                expected_source_fingerprint=lifecycle_operation.source_fingerprint,
            )
            if lifecycle_operation.state is WorktreeLifecycleState.PREPARED:
                lifecycle_operation = lifecycle.update_state(
                    WorktreeLifecycleScope.SESSION_CREATE,
                    lifecycle_operation_id,
                    WorktreeLifecycleState.WORKTREE_CREATED,
                )
            project = manager.project(worktree.project_id)
            session_id = lifecycle_operation.session_id
            if session_id is None:
                raise ApplicationError(
                    "WORKTREE_RECOVERY_REQUIRED",
                    "session create lifecycle has no session id",
                )
            existing_session = self._repository.read_session(session_id)
            if existing_session is None:
                try:
                    mutation = self._repository.create_session(
                        project.workspace_root,
                        worktree_id=worktree.id,
                        execution_mode=SessionExecutionMode.WORKTREE,
                        project_id=project.id,
                        operation_id=None,
                        session_id=session_id,
                    )
                except TypeError as error:
                    if not any(
                        field in str(error)
                        for field in ("execution_mode", "project_id", "session_id")
                    ):
                        raise
                    # Keep narrow compatibility with older injected repository
                    # seams used by failure tests.
                    mutation = self._repository.create_session(
                        project.workspace_root,
                        worktree_id=worktree.id,
                        operation_id=None,
                    )
                session = mutation.value
            else:
                if (
                    existing_session.worktree_id != worktree.id
                    or existing_session.execution_mode
                    is not SessionExecutionMode.WORKTREE
                ):
                    raise ApplicationError(
                        "WORKTREE_RECOVERY_REQUIRED",
                        "session lifecycle binding does not match",
                    )
                session = existing_session
            if lifecycle_operation.state in {
                WorktreeLifecycleState.PREPARED,
                WorktreeLifecycleState.WORKTREE_CREATED,
            }:
                lifecycle_operation = lifecycle.update_state(
                    WorktreeLifecycleScope.SESSION_CREATE,
                    lifecycle_operation_id,
                    WorktreeLifecycleState.SESSION_CREATED,
                )
            lifecycle_operation = lifecycle.update_state(
                WorktreeLifecycleScope.SESSION_CREATE,
                lifecycle_operation_id,
                WorktreeLifecycleState.COMPLETED,
            )
            manager.touch_last_used(worktree.id)
            if self._retention is not None:
                try:
                    self._retention.reconcile()
                except Exception:
                    self._logger.exception(
                        "Worktree retention reconciliation after create failed",
                        extra={"worktree_id": worktree.id},
                    )
            result = _result(
                SessionCreateResponseDto,
                self._project_session(self._projection_for_session(session.id)),
            )
            if request.operation_id is not None:
                return self._record_session_operation(
                    request.operation_id,
                    operation_request,
                    result,
                )
            return result
        except WorktreeError as error:
            if (
                error.code == "worktree_cleanup_required"
                and lifecycle_operation is not None
                and lifecycle_operation.state is not WorktreeLifecycleState.CLEANUP_REQUIRED
            ):
                try:
                    lifecycle.update_state(
                        WorktreeLifecycleScope.SESSION_CREATE,
                        lifecycle_operation_id,
                        WorktreeLifecycleState.CLEANUP_REQUIRED,
                        error_code=error.code,
                    )
                except Exception:
                    pass
            raise ApplicationError(_worktree_error_code(error), str(error)) from error
        except OperationConflictError as error:
            raise ApplicationError("OPERATION_ID_REUSED", str(error)) from error
        except OperationInProgressError as error:
            raise ApplicationError("OPERATION_IN_PROGRESS", str(error)) from error
        except StorageError as error:
            if worktree is not None:
                self._compensate_managed_worktree(manager, worktree)
            try:
                lifecycle.update_state(
                    WorktreeLifecycleScope.SESSION_CREATE,
                    lifecycle_operation_id,
                    WorktreeLifecycleState.CLEANUP_REQUIRED,
                    error_code="session_persistence_failed",
                )
            except Exception:
                pass
            raise ApplicationError(
                "SESSION_PERSISTENCE_FAILED", str(error)
            ) from error
        except Exception as error:
            if isinstance(error, WorkspaceBoundaryError):
                raise ApplicationError(
                    "WORKSPACE_BOUNDARY_VIOLATION", str(error)
                ) from error
            if isinstance(error, ResourceNotFoundError):
                raise ApplicationError("RESOURCE_NOT_FOUND", str(error)) from error
            if isinstance(error, StorageError):
                raise ApplicationError(
                    "SESSION_PERSISTENCE_FAILED", str(error)
                ) from error
            raise

    def _record_session_operation(
        self,
        operation_id: str,
        operation_request: dict[str, object],
        result: SessionCreateResponseDto,
    ) -> SessionCreateResponseDto:
        try:
            recorded = self._store.record_operation_result(
                operation_id,
                "session/create",
                operation_request,
                result.to_json_value(),
            )
        except OperationConflictError as error:
            raise ApplicationError("OPERATION_ID_REUSED", str(error)) from error
        except OperationInProgressError as error:
            raise ApplicationError("OPERATION_IN_PROGRESS", str(error)) from error
        return _result(SessionCreateResponseDto, recorded)

    def _compensate_managed_worktree(
        self, manager: ManagedWorktreePort, worktree: Worktree
    ) -> None:
        try:
            manager.rollback_create(worktree.id)
        except Exception as error:
            self._logger.error(
                "session create worktree compensation needs recovery",
                extra={
                    "worktree_id": worktree.id,
                    "operation": "session/create",
                    "result": "recovery-needed",
                    "error": str(error),
                },
            )

    def list(self, request: SessionListRequestDto) -> SessionListResponseDto:
        try:
            page = self._repository.list_session_projections(
                limit=request.limit,
                cursor=request.cursor,
            )
        except InvalidCursorError as error:
            raise ApplicationInvalidParamsError("INVALID_CURSOR", str(error)) from error
        value: dict[str, object] = {
            "items": [self._project_session(projection) for projection in page.items]
        }
        if page.next_cursor is not None:
            value["nextCursor"] = page.next_cursor
        return _result(SessionListResponseDto, value)

    def read_snapshot(
        self, request: SessionReadRequestDto
    ) -> SessionReadResponseDto:
        projection = self._repository.read_session_projection(request.session_id)
        if projection is None:
            raise ApplicationError("RESOURCE_NOT_FOUND", "session not found")
        self._activate_projection(projection, best_effort=True)
        try:
            snapshot = self._store.read_session_snapshot(
                request.session_id,
                item_limit=request.item_limit,
                before_item_id=request.before_item_id,
            )
        except ResourceNotFoundError as error:
            raise ApplicationError("RESOURCE_NOT_FOUND", str(error)) from error
        projected_snapshot = dict(snapshot)
        projected_snapshot["session"] = self._project_session(projection)
        return _result(SessionReadResponseDto, projected_snapshot)

    def git_status(
        self, request: SessionGitStatusRequestDto
    ) -> SessionGitStatusResponseDto:
        session = self._repository.read_session(request.session_id)
        if session is None:
            raise ApplicationError("RESOURCE_NOT_FOUND", "session not found")
        manager = self._worktree_manager
        if manager is None:
            if session.worktree_id is None:
                raise ApplicationError(
                    "GIT_WORKTREE_NOT_MANAGED", "session has no managed Worktree"
                )
            raise ApplicationError(
                "INTERNAL_ERROR", "Session Git application boundary is unavailable"
            )
        try:
            if session.worktree_id is not None:
                status = manager.status(session.worktree_id)
            else:
                resolution = manager.resolve_project(session.workspace_root)
                if resolution.git is None:
                    raise WorktreeError("not_a_git_repository")
                status = manager.local_status(Path(resolution.git.repository_root))
        except WorktreeError as error:
            raise ApplicationError(_git_review_error_code(error), str(error)) from error
        return _result(
            SessionGitStatusResponseDto,
            {
                "worktreeId": status.worktree_id,
                "branch": status.branch,
                "head": status.head,
                "baseRef": status.base_ref,
                "baseCommit": status.base_commit,
                "dirty": status.dirty,
                "stagedCount": status.staged_count,
                "unstagedCount": status.unstaged_count,
                "untrackedCount": status.untracked_count,
                "conflictCount": status.conflict_count,
                "stagedFiles": list(status.staged_files),
                "unstagedFiles": list(status.unstaged_files),
                "untrackedFiles": list(status.untracked_files),
                "conflictFiles": list(status.conflict_files),
                "observedAt": _timestamp_millis(status.observed_at),
            },
        )

    def git_diff(
        self, request: SessionGitDiffRequestDto
    ) -> SessionGitDiffResponseDto:
        session = self._repository.read_session(request.session_id)
        if session is None:
            raise ApplicationError("RESOURCE_NOT_FOUND", "session not found")
        manager = self._worktree_manager
        if manager is None:
            if session.worktree_id is None:
                raise ApplicationError(
                    "GIT_WORKTREE_NOT_MANAGED", "session has no managed Worktree"
                )
            raise ApplicationError(
                "INTERNAL_ERROR", "Session Git application boundary is unavailable"
            )
        try:
            scope = DiffScope(request.scope)
            if session.worktree_id is not None:
                diff = manager.diff(
                    session.worktree_id, scope=scope, path=request.path
                )
            else:
                resolution = manager.resolve_project(session.workspace_root)
                if resolution.git is None:
                    raise WorktreeError("not_a_git_repository")
                diff = manager.local_diff(
                    Path(resolution.git.repository_root),
                    scope=scope,
                    path=request.path,
                )
        except WorktreeError as error:
            raise ApplicationError(_git_review_error_code(error), str(error)) from error
        return _result(
            SessionGitDiffResponseDto,
            {
                "scope": diff.scope.value,
                "baseCommit": diff.base_commit,
                "head": diff.head,
                "dirty": diff.dirty,
                "changedFiles": list(diff.changed_files),
                "unifiedDiff": diff.unified_diff,
                "diffHash": hashlib.sha256(
                    diff.unified_diff.encode("utf-8")
                ).hexdigest(),
                "truncated": diff.truncated,
                "observedAt": _timestamp_millis(diff.observed_at),
            },
        )

    def git_stage(
        self, request: SessionGitStageRequestDto
    ) -> SessionGitStageResponseDto:
        if self._git_workflow is None:
            raise ApplicationError(
                "INTERNAL_ERROR", "Session Git workflow boundary is unavailable"
            )
        return self._execute_git_mutation(
            request,
            scope="session/gitStage",
            result_type=SessionGitStageResponseDto,
            preflight=lambda: self._git_workflow.preflight_stage(request),
            execute=self._git_workflow.stage,
        )

    def git_unstage(
        self, request: SessionGitUnstageRequestDto
    ) -> SessionGitUnstageResponseDto:
        if self._git_workflow is None:
            raise ApplicationError(
                "INTERNAL_ERROR", "Session Git workflow boundary is unavailable"
            )
        return self._execute_git_mutation(
            request,
            scope="session/gitUnstage",
            result_type=SessionGitUnstageResponseDto,
            preflight=lambda: self._git_workflow.preflight_unstage(request),
            execute=self._git_workflow.unstage,
        )

    def git_commit(
        self, request: SessionGitCommitRequestDto
    ) -> SessionGitCommitResponseDto:
        if self._git_workflow is None:
            raise ApplicationError(
                "INTERNAL_ERROR", "Session Git workflow boundary is unavailable"
            )
        return self._execute_git_mutation(
            request,
            scope="session/gitCommit",
            result_type=SessionGitCommitResponseDto,
            preflight=lambda: self._git_workflow.preflight_commit(request),
            execute=self._git_workflow.commit,
        )

    def git_discard(
        self, request: SessionGitDiscardRequestDto
    ) -> SessionGitDiscardResponseDto:
        if self._git_workflow is None:
            raise ApplicationError("INTERNAL_ERROR")
        return self._execute_git_mutation(
            request,
            scope="session/gitDiscard",
            result_type=SessionGitDiscardResponseDto,
            preflight=lambda: self._git_workflow.preflight_discard(request),
            execute=self._git_workflow.discard,
        )

    def git_merge(
        self, request: SessionGitMergeRequestDto
    ) -> SessionGitMergeResponseDto:
        if self._git_workflow is None:
            raise ApplicationError("INTERNAL_ERROR")
        return self._execute_git_mutation(
            request,
            scope="session/gitMerge",
            result_type=SessionGitMergeResponseDto,
            preflight=lambda: self._git_workflow.preflight_merge(request),
            execute=self._git_workflow.merge,
        )

    def git_merge_abort(
        self, request: SessionGitMergeAbortRequestDto
    ) -> SessionGitMergeAbortResponseDto:
        if self._git_workflow is None:
            raise ApplicationError("INTERNAL_ERROR")
        return self._execute_git_mutation(
            request,
            scope="session/gitMergeAbort",
            result_type=SessionGitMergeAbortResponseDto,
            preflight=lambda: self._git_workflow.preflight_merge_abort(request),
            execute=self._git_workflow.merge_abort,
        )

    def git_rebase(
        self, request: SessionGitRebaseRequestDto
    ) -> SessionGitRebaseResponseDto:
        if self._git_workflow is None:
            raise ApplicationError("INTERNAL_ERROR")
        return self._execute_git_mutation(
            request,
            scope="session/gitRebase",
            result_type=SessionGitRebaseResponseDto,
            preflight=lambda: self._git_workflow.preflight_rebase(request),
            execute=self._git_workflow.rebase,
        )

    def git_rebase_continue(
        self, request: SessionGitRebaseContinueRequestDto
    ) -> SessionGitRebaseContinueResponseDto:
        if self._git_workflow is None:
            raise ApplicationError("INTERNAL_ERROR")
        return self._execute_git_mutation(
            request,
            scope="session/gitRebaseContinue",
            result_type=SessionGitRebaseContinueResponseDto,
            preflight=lambda: self._git_workflow.preflight_rebase_continue(request),
            execute=self._git_workflow.rebase_continue,
        )

    def git_rebase_abort(
        self, request: SessionGitRebaseAbortRequestDto
    ) -> SessionGitRebaseAbortResponseDto:
        if self._git_workflow is None:
            raise ApplicationError("INTERNAL_ERROR")
        return self._execute_git_mutation(
            request,
            scope="session/gitRebaseAbort",
            result_type=SessionGitRebaseAbortResponseDto,
            preflight=lambda: self._git_workflow.preflight_rebase_abort(request),
            execute=self._git_workflow.rebase_abort,
        )

    def git_remote_status(
        self, request: SessionGitRemoteStatusRequestDto
    ) -> SessionGitRemoteStatusResponseDto:
        if self._git_workflow is None:
            raise ApplicationError("INTERNAL_ERROR")
        return self._git_workflow.remote_status(request.session_id)

    def prepare_git_fetch(
        self, request: SessionGitFetchRequestDto, *, request_id: str
    ) -> SessionGitFetchResponseDto | DeferredGitFetch:
        if self._git_workflow is None:
            raise ApplicationError("INTERNAL_ERROR")
        scope = "session/gitFetch"
        operation_request = request.to_json_value()
        operation_request.pop("operationId", None)
        with self._lifecycle.hold_operation(scope, request.operation_id):
            replay = self._git_operation_replay(
                request.operation_id,
                scope,
                operation_request,
                SessionGitFetchResponseDto,
            )
            if replay is not None:
                return replay
            with self._lifecycle.hold(request.session_id):
                plan = self._git_workflow.preflight_fetch(
                    request.session_id, request.remote
                )
                try:
                    reservation = self._store.prepare_deferred_external_operation(
                        request_id=request_id,
                        operation_id=request.operation_id,
                        scope=scope,
                        request=operation_request,
                    )
                except OperationConflictError as error:
                    raise ApplicationError("OPERATION_ID_REUSED") from error
                except OperationInProgressError as error:
                    raise ApplicationError("OPERATION_IN_PROGRESS") from error
            if reservation.replay_result is not None:
                return _result(
                    SessionGitFetchResponseDto, reservation.replay_result
                )
            if not reservation.created or reservation.operation is None:
                raise ApplicationError("OPERATION_IN_PROGRESS")
            return DeferredGitFetch(
                async_operation_id=reservation.operation.id,
                operation_id=request.operation_id,
                request=operation_request,
                plan=plan,
                _application=self,
            )

    def _run_git_fetch(
        self, deferred: DeferredGitFetch, cancel: threading.Event
    ) -> SessionGitFetchResponseDto:
        if cancel.is_set():
            error = ApplicationError("GIT_REMOTE_CANCELED")
            self._finalize_git_external_failure(deferred, error)
            raise error
        self._store.start_async_operation(deferred.async_operation_id)
        try:
            with self._lifecycle.hold(deferred.plan.session.id):
                self._repository.assert_session_deletable(deferred.plan.session.id)
                result = self._git_workflow.fetch(deferred.plan, cancel)  # type: ignore[union-attr]
            completed = self._store.complete_operation(
                deferred.operation_id,
                "session/gitFetch",
                deferred.request,
                result.to_json_value(),
            )
            self._store.complete_async_operation(
                deferred.async_operation_id, completed
            )
            return _result(SessionGitFetchResponseDto, completed)
        except ApplicationError as error:
            self._finalize_git_external_failure(deferred, error)
            raise
        except SessionActiveError as error:
            failure = ApplicationError("GIT_WORKFLOW_BUSY")
            self._finalize_git_external_failure(deferred, failure)
            raise failure from error

    def prepare_git_pull(
        self, request: SessionGitPullRequestDto, *, request_id: str
    ) -> SessionGitPullResponseDto | DeferredGitPull:
        if self._git_workflow is None:
            raise ApplicationError("INTERNAL_ERROR")
        scope = "session/gitPull"
        operation_request = request.to_json_value()
        operation_request.pop("operationId", None)
        with self._lifecycle.hold_operation(scope, request.operation_id):
            replay = self._git_operation_replay(
                request.operation_id,
                scope,
                operation_request,
                SessionGitPullResponseDto,
            )
            if replay is not None:
                return replay
            with self._lifecycle.hold(request.session_id):
                plan = self._git_workflow.preflight_pull(request)
                try:
                    reservation = self._store.prepare_deferred_external_operation(
                        request_id=request_id,
                        operation_id=request.operation_id,
                        scope=scope,
                        request=operation_request,
                    )
                except OperationConflictError as error:
                    raise ApplicationError("OPERATION_ID_REUSED") from error
                except OperationInProgressError as error:
                    raise ApplicationError("OPERATION_IN_PROGRESS") from error
            if reservation.replay_result is not None:
                return _result(
                    SessionGitPullResponseDto, reservation.replay_result
                )
            if not reservation.created or reservation.operation is None:
                raise ApplicationError("OPERATION_IN_PROGRESS")
            return DeferredGitPull(
                async_operation_id=reservation.operation.id,
                operation_id=request.operation_id,
                request=operation_request,
                plan=plan,
                _application=self,
            )

    def _run_git_pull(
        self, deferred: DeferredGitPull, cancel: threading.Event
    ) -> SessionGitPullResponseDto:
        if cancel.is_set():
            error = ApplicationError("GIT_REMOTE_CANCELED")
            self._finalize_git_external_failure(deferred, error)
            raise error
        self._store.start_async_operation(deferred.async_operation_id)
        try:
            with self._lifecycle.hold(deferred.plan.session.id):
                self._repository.assert_session_deletable(deferred.plan.session.id)
                result = self._git_workflow.pull(deferred.plan, cancel)  # type: ignore[union-attr]
            completed = self._store.complete_operation(
                deferred.operation_id,
                "session/gitPull",
                deferred.request,
                result.to_json_value(),
            )
            self._store.complete_async_operation(
                deferred.async_operation_id, completed
            )
            return _result(SessionGitPullResponseDto, completed)
        except ApplicationError as error:
            self._finalize_git_external_failure(deferred, error)
            raise
        except SessionActiveError as error:
            failure = ApplicationError("GIT_WORKFLOW_BUSY")
            self._finalize_git_external_failure(deferred, failure)
            raise failure from error

    def prepare_git_push(
        self, request: SessionGitPushRequestDto, *, request_id: str
    ) -> SessionGitPushResponseDto | DeferredGitPush:
        if self._git_workflow is None:
            raise ApplicationError("INTERNAL_ERROR")
        scope = "session/gitPush"
        operation_request = request.to_json_value()
        operation_request.pop("operationId", None)
        with self._lifecycle.hold_operation(scope, request.operation_id):
            replay = self._git_operation_replay(
                request.operation_id,
                scope,
                operation_request,
                SessionGitPushResponseDto,
            )
            if replay is not None:
                return replay
            with self._lifecycle.hold(request.session_id):
                plan = self._git_workflow.preflight_push(request)
                try:
                    reservation = self._store.prepare_deferred_external_operation(
                        request_id=request_id,
                        operation_id=request.operation_id,
                        scope=scope,
                        request=operation_request,
                    )
                except OperationConflictError as error:
                    raise ApplicationError("OPERATION_ID_REUSED") from error
                except OperationInProgressError as error:
                    raise ApplicationError("OPERATION_IN_PROGRESS") from error
            if reservation.replay_result is not None:
                return _result(
                    SessionGitPushResponseDto, reservation.replay_result
                )
            if not reservation.created or reservation.operation is None:
                raise ApplicationError("OPERATION_IN_PROGRESS")
            return DeferredGitPush(
                async_operation_id=reservation.operation.id,
                operation_id=request.operation_id,
                request=operation_request,
                plan=plan,
                _application=self,
            )

    def _run_git_push(
        self, deferred: DeferredGitPush, cancel: threading.Event
    ) -> SessionGitPushResponseDto:
        if cancel.is_set():
            error = ApplicationError("GIT_REMOTE_CANCELED")
            self._finalize_git_external_failure(deferred, error)
            raise error
        self._store.start_async_operation(deferred.async_operation_id)
        try:
            with self._lifecycle.hold(deferred.plan.session.id):
                self._repository.assert_session_deletable(deferred.plan.session.id)
                result = self._git_workflow.push(deferred.plan, cancel)  # type: ignore[union-attr]
            completed = self._store.complete_operation(
                deferred.operation_id,
                "session/gitPush",
                deferred.request,
                result.to_json_value(),
            )
            self._store.complete_async_operation(
                deferred.async_operation_id, completed
            )
            return _result(SessionGitPushResponseDto, completed)
        except ApplicationError as error:
            self._finalize_git_external_failure(deferred, error)
            raise
        except SessionActiveError as error:
            failure = ApplicationError("GIT_WORKFLOW_BUSY")
            self._finalize_git_external_failure(deferred, failure)
            raise failure from error

    def _cancel_git_external_before_start(
        self,
        deferred: DeferredGitFetch | DeferredGitPull | DeferredGitPush,
    ) -> None:
        error = ApplicationError("GIT_REMOTE_CANCELED")
        self._finalize_git_external_failure(deferred, error)

    def _finalize_git_external_failure(
        self,
        deferred: DeferredGitFetch | DeferredGitPull | DeferredGitPush,
        error: ApplicationError,
    ) -> bool:
        try:
            self._store.fail_operation(
                deferred.operation_id,
                (
                    "session/gitFetch"
                    if isinstance(deferred, DeferredGitFetch)
                    else "session/gitPull"
                    if isinstance(deferred, DeferredGitPull)
                    else "session/gitPush"
                ),
                deferred.request,
                error_code=error.code,
                side_effects_may_exist=(
                    error.code == "GIT_REMOTE_OUTCOME_UNCERTAIN"
                ),
            )
        except OperationFailedError:
            pass
        except (OperationConflictError, OperationInProgressError, StorageError):
            return False
        try:
            if error.code == "GIT_REMOTE_CANCELED":
                self._store.cancel_async_operation(deferred.async_operation_id)
            else:
                self._store.fail_async_operation(
                    deferred.async_operation_id, error.code
                )
        except (InvalidRunStateError, StorageError):
            pass
        return True

    def _execute_git_mutation(
        self,
        request: (
            SessionGitStageRequestDto
            | SessionGitUnstageRequestDto
            | SessionGitCommitRequestDto
            | SessionGitDiscardRequestDto
            | SessionGitMergeRequestDto
            | SessionGitMergeAbortRequestDto
            | SessionGitRebaseRequestDto
            | SessionGitRebaseContinueRequestDto
            | SessionGitRebaseAbortRequestDto
        ),
        *,
        scope: str,
        result_type: type[ResultT],
        preflight: Callable[[], GitPlanT],
        execute: Callable[[GitPlanT], ResultT],
    ) -> ResultT:
        operation_request = request.to_json_value()
        operation_request.pop("operationId", None)
        operation_guard = (
            self._lifecycle.hold_operation(scope, request.operation_id)
            if request.operation_id is not None
            else nullcontext()
        )
        with operation_guard:
            if request.operation_id is not None:
                replay = self._git_operation_replay(
                    request.operation_id,
                    scope,
                    operation_request,
                    result_type,
                )
                if replay is not None:
                    return replay
            with self._lifecycle.hold(request.session_id):
                plan = preflight()
                if request.operation_id is not None:
                    try:
                        self._store.prepare_operation(
                            request.operation_id, scope, operation_request
                        )
                    except OperationConflictError as error:
                        raise ApplicationError("OPERATION_ID_REUSED") from error
                    except OperationInProgressError as error:
                        raise ApplicationError("OPERATION_IN_PROGRESS") from error
                result = execute(plan)
            if request.operation_id is None:
                return result
            try:
                completed = self._store.complete_operation(
                    request.operation_id,
                    scope,
                    operation_request,
                    result.to_json_value(),
                )
            except OperationConflictError as error:
                raise ApplicationError("OPERATION_ID_REUSED", str(error)) from error
            except OperationInProgressError as error:
                raise ApplicationError("OPERATION_IN_PROGRESS", str(error)) from error
            return _result(result_type, completed)

    def _git_operation_replay(
        self,
        operation_id: str,
        scope: str,
        request: dict[str, object],
        result_type: type[ResultT],
    ) -> ResultT | None:
        try:
            replay = self._store.operation_result(operation_id, scope, request)
        except OperationConflictError as error:
            raise ApplicationError("OPERATION_ID_REUSED") from error
        except OperationFailedError as error:
            raise ApplicationError(error.code) from error
        except OperationInProgressError as error:
            raise ApplicationError("OPERATION_IN_PROGRESS") from error
        return _result(result_type, replay) if replay is not None else None

    def handoff(
        self, request: SessionHandoffRequestDto
    ) -> SessionHandoffResponseDto:
        manager = self._require_manager()
        target_mode = SessionExecutionMode(request.target)
        scope = (
            SessionHandoffScope.LOCAL
            if target_mode is SessionExecutionMode.LOCAL
            else SessionHandoffScope.WORKTREE
        )
        operation_id = request.operation_id or f"handoff-{uuid.uuid4().hex}"
        with self._lifecycle.hold_operation(scope.value, operation_id):
            with self._lifecycle.hold(request.session_id):
                session = self._repository.read_session(request.session_id)
                if session is None:
                    raise ApplicationError("RESOURCE_NOT_FOUND", "session not found")
                try:
                    handoffs = self._store.session_handoff_repository()
                    operation = handoffs.read(scope, operation_id)
                    if (
                        operation is not None
                        and (
                            operation.session_id != request.session_id
                            or operation.target_mode is not target_mode
                        )
                    ):
                        raise ApplicationError(
                            "OPERATION_ID_REUSED",
                            "handoff operation does not match the Session",
                        )
                    if session.execution_mode is target_mode and operation is None:
                        raise ApplicationError(
                            "HANDOFF_NOT_SUPPORTED",
                            "Session is already using the requested execution mode",
                        )
                    self._repository.assert_session_idle(request.session_id)
                    if operation is None:
                        plan = self._build_handoff_plan(
                            session, target_mode, operation_id
                        )
                        now = _handoff_timestamp()
                        operation = handoffs.prepare(
                            SessionHandoffOperation(
                                scope=scope,
                                operation_id=operation_id,
                                state=SessionHandoffState.PREPARED,
                                **{
                                    key: value
                                    for key, value in plan.model_dump().items()
                                    if key != "operation_id"
                                },
                                created_at=now,
                                updated_at=now,
                            )
                        )
                    if operation.state is SessionHandoffState.COMPLETED:
                        return self._handoff_response(request.session_id)
                    if operation.state is SessionHandoffState.CLEANUP_REQUIRED:
                        raise ApplicationError(
                            _handoff_error_code(operation.error_code),
                            operation.error_code or "handoff recovery is required",
                        )
                    result = self._resume_handoff(operation)
                    return self._handoff_response(result.id)
                except ApplicationError:
                    raise
                except SessionActiveError as error:
                    raise ApplicationError("SESSION_HAS_ACTIVE_RUN", str(error)) from error
                except WorktreeError as error:
                    self._mark_handoff_failure(operation if "operation" in locals() else None, error)
                    raise ApplicationError(_handoff_error_code(error.code), str(error)) from error
                except StorageError as error:
                    self._mark_handoff_failure(operation if "operation" in locals() else None, error)
                    raise ApplicationError("WORKTREE_RECOVERY_REQUIRED", str(error)) from error

    def restore_worktree(
        self, request: SessionRestoreWorktreeRequestDto
    ) -> SessionRestoreWorktreeResponseDto:
        self._require_manager()
        session = self._repository.read_session(request.session_id)
        if session is None:
            raise ApplicationError("RESOURCE_NOT_FOUND", "session not found")
        worktree_id = session.associated_worktree_id
        if worktree_id is None or self._retention is None:
            raise ApplicationError(
                "WORKTREE_RESTORE_REQUIRED",
                "Session has no restorable managed Worktree snapshot",
            )
        try:
            self._retention.restore_worktree(
                worktree_id, operation_id=request.operation_id
            )
        except WorktreeError as error:
            raise ApplicationError(
                _handoff_error_code(error.code), str(error)
            ) from error
        try:
            self._retention.reconcile()
        except Exception:
            self._logger.exception(
                "Worktree retention reconciliation after restore failed",
                extra={"worktree_id": worktree_id},
            )
        value = self._project_session(self._projection_for_session(session.id))
        value["sessionId"] = session.id
        value["worktreeId"] = worktree_id
        return _result(SessionRestoreWorktreeResponseDto, value)

    def recover_handoffs(self) -> tuple[SessionHandoffOperation, ...]:
        """Resume durable handoffs after Runtime restart.

        The operation row is the only recovery cursor.  A Git step may have
        completed before the corresponding state update, so each materializer
        verifies the expected target state before it repeats any operation.
        """

        repository = self._store.session_handoff_repository()
        recovered: list[SessionHandoffOperation] = []
        for operation in repository.list_unfinished():
            try:
                with self._lifecycle.hold(operation.session_id):
                    current = repository.read(
                        operation.scope, operation.operation_id
                    )
                    if current is None:
                        continue
                    self._resume_handoff(current)
                    completed = repository.read(
                        operation.scope, operation.operation_id
                    )
                    if completed is not None:
                        recovered.append(completed)
            except Exception as error:
                current = repository.read(operation.scope, operation.operation_id)
                if current is None or current.state in {
                    SessionHandoffState.COMPLETED,
                    SessionHandoffState.CLEANUP_REQUIRED,
                }:
                    continue
                try:
                    repository.update_state(
                        current.scope,
                        current.operation_id,
                        SessionHandoffState.CLEANUP_REQUIRED,
                        error_code=_handoff_exception_code(error),
                    )
                except Exception:
                    self._logger.exception(
                        "session handoff recovery could not persist cleanup state",
                        extra={
                            "session_id": current.session_id,
                            "operation_id": current.operation_id,
                        },
                    )
        return tuple(recovered)

    def _handoff_response(self, session_id: str) -> SessionHandoffResponseDto:
        projection = self._projection_for_session(session_id)
        self._activate_projection(projection)
        value = self._project_session(projection)
        value["sessionId"] = session_id
        worktree = value.get("worktree")
        value["worktreeId"] = (
            worktree.get("worktreeId")
            if isinstance(worktree, dict)
            else None
        )
        return _result(SessionHandoffResponseDto, value)

    def _activate_projection(
        self, projection: SessionProjection, *, best_effort: bool = False
    ) -> None:
        runtime = self._repository_runtime
        if runtime is None:
            return
        if (
            projection.worktree is not None
            and projection.worktree.state is not WorktreeState.ACTIVE
        ):
            return
        root = (
            Path(projection.worktree.worktree_root)
            if projection.worktree is not None
            else Path(projection.session.workspace_root)
        )
        if best_effort and not root.is_dir():
            return
        try:
            runtime.activate_workspace(root)
        except (OSError, ValueError):
            if not best_effort:
                raise
            self._logger.info(
                "repository_session_prewarm_skipped",
                extra={"session_id": projection.session.id},
            )

    def _build_handoff_plan(
        self,
        session: Session,
        target_mode: SessionExecutionMode,
        operation_id: str,
    ) -> HandoffPlan:
        manager = self._require_manager()
        resolution = manager.resolve_project(session.workspace_root)
        if resolution.git is None:
            raise WorktreeError("handoff_not_supported")
        project = resolution.project
        local_root = Path(resolution.git.repository_root)
        if session.execution_mode is SessionExecutionMode.LOCAL:
            source = manager.source_snapshot(local_root, include_local_changes=True)
            if session.associated_worktree_id is None:
                prepared = manager.prepare_create(source.discovery.repository_root)
                target_root = Path(prepared.worktree_root)
                target_head = prepared.base_commit
                target_branch = prepared.checkout_branch
                target_worktree_new = True
                target_base_ref = prepared.base_ref
                target_base_commit = prepared.base_commit
                target_fingerprint = _planned_target_fingerprint(prepared.id)
                associated_worktree_id = prepared.id
            else:
                associated_worktree_id = session.associated_worktree_id
                worktree = self._read_handoff_worktree(associated_worktree_id)
                if worktree.project_id != project.id:
                    raise WorktreeError("handoff_target_changed")
                manager.status(worktree.id)
                target = manager.source_snapshot(
                    Path(worktree.worktree_root), include_local_changes=True
                )
                self._assert_inactive_worktree(session.id, worktree.id, target)
                if target.status.dirty:
                    raise WorktreeError("handoff_target_changed")
                target_root = Path(worktree.worktree_root)
                target_head = target.head
                target_branch = target.branch
                target_worktree_new = False
                target_base_ref = worktree.base_ref
                target_base_commit = worktree.base_commit
                target_fingerprint = target.fingerprint
            if source.discovery.git_common_dir != resolution.git.git_common_dir:
                raise WorktreeError("handoff_git_conflict")
            return HandoffPlan(
                operation_id=operation_id,
                session_id=session.id,
                project_id=project.id,
                source_mode=SessionExecutionMode.LOCAL,
                target_mode=target_mode,
                source_root=str(local_root),
                target_root=str(target_root),
                source_common_dir=source.discovery.git_common_dir,
                target_common_dir=resolution.git.git_common_dir,
                associated_worktree_id=associated_worktree_id,
                target_worktree_new=target_worktree_new,
                target_base_ref=target_base_ref,
                target_base_commit=target_base_commit,
                source_head=source.head,
                source_branch=source.branch,
                source_dirty=source.status.dirty,
                source_fingerprint=source.fingerprint,
                target_head=target_head,
                target_branch=target_branch,
                target_dirty=False,
                target_fingerprint=target_fingerprint,
            )

        if session.worktree_id is None or session.associated_worktree_id != session.worktree_id:
            raise WorktreeError("handoff_recovery_required")
        worktree = self._read_handoff_worktree(session.worktree_id)
        if worktree.project_id != project.id:
            raise WorktreeError("handoff_git_conflict")
        manager.status(worktree.id)
        source = manager.source_snapshot(
            Path(worktree.worktree_root), include_local_changes=True
        )
        target = manager.source_snapshot(local_root, include_local_changes=True)
        if target.status.dirty or target.status.conflict_paths:
            raise WorktreeError("handoff_local_conflict")
        if source.discovery.git_common_dir != target.discovery.git_common_dir:
            raise WorktreeError("handoff_git_conflict")
        return HandoffPlan(
            operation_id=operation_id,
            session_id=session.id,
            project_id=project.id,
            source_mode=SessionExecutionMode.WORKTREE,
            target_mode=target_mode,
            source_root=worktree.worktree_root,
            target_root=str(local_root),
            source_common_dir=source.discovery.git_common_dir,
            target_common_dir=target.discovery.git_common_dir,
            associated_worktree_id=worktree.id,
            target_worktree_new=False,
            target_base_ref=None,
            target_base_commit=None,
            source_head=source.head,
            source_branch=source.branch,
            source_dirty=source.status.dirty,
            source_fingerprint=source.fingerprint,
            target_head=target.head,
            target_branch=target.branch,
            target_dirty=target.status.dirty,
            target_fingerprint=target.fingerprint,
        )

    def _resume_handoff(
        self, operation: SessionHandoffOperation
    ) -> Session:
        manager = self._require_manager()
        repository = self._store.session_handoff_repository()
        current = operation
        source: GitSourceSnapshot | None = None
        materialized_target: GitSourceSnapshot | None = None
        if current.state in {
            SessionHandoffState.PREPARED,
            SessionHandoffState.SOURCE_CAPTURED,
        }:
            source = manager.source_snapshot(
                Path(current.source_root), include_local_changes=True
            )
            if source.fingerprint != current.source_fingerprint:
                if current.state is not SessionHandoffState.SOURCE_CAPTURED:
                    raise WorktreeError("handoff_source_changed")
                materialized_target = manager.source_snapshot(
                    Path(current.target_root), include_local_changes=True
                )
                if not _matches_handoff_transfer(
                    current, source, materialized_target
                ):
                    raise WorktreeError("handoff_source_changed")
        if current.state is SessionHandoffState.PREPARED:
            current = repository.update_state(
                current.scope,
                current.operation_id,
                SessionHandoffState.SOURCE_CAPTURED,
            )
        if current.state is SessionHandoffState.SOURCE_CAPTURED:
            if source is None:
                source = manager.source_snapshot(
                    Path(current.source_root), include_local_changes=True
                )
            if materialized_target is not None:
                target = materialized_target
            elif current.target_mode is SessionExecutionMode.WORKTREE:
                target = self._materialize_worktree_target(current, source)
            else:
                target = self._materialize_local_target(current, source)
            current = repository.update_state(
                current.scope,
                current.operation_id,
                SessionHandoffState.TARGET_MATERIALIZED,
                target_after_head=target.head,
                target_after_branch=target.branch,
                target_after_fingerprint=target.fingerprint,
            )
        if current.state is SessionHandoffState.TARGET_MATERIALIZED:
            source_after_head: str | None = None
            source_after_branch: str | None = None
            source_after_fingerprint: str | None = None
            if current.source_mode is SessionExecutionMode.WORKTREE:
                if current.source_after_fingerprint is not None:
                    source_after = manager.source_snapshot(
                        Path(current.source_root), include_local_changes=True
                    )
                    if source_after.fingerprint != current.source_after_fingerprint:
                        raise WorktreeError("handoff_target_changed")
                else:
                    manager.clean_worktree_after_handoff(
                        current.associated_worktree_id,
                        expected_head=current.source_head,
                    )
                    source_after = manager.source_snapshot(
                        Path(current.source_root), include_local_changes=True
                    )
                source_after_head = source_after.head
                source_after_branch = source_after.branch
                source_after_fingerprint = source_after.fingerprint
            current = repository.update_state(
                current.scope,
                current.operation_id,
                SessionHandoffState.TARGET_MATERIALIZED,
                source_after_head=source_after_head,
                source_after_branch=source_after_branch,
                source_after_fingerprint=source_after_fingerprint,
            )
            if (
                current.source_mode is SessionExecutionMode.WORKTREE
                and current.source_branch is not None
            ):
                manager.release_user_branch_after_handoff(
                    current.associated_worktree_id,
                    expected_branch=current.source_branch,
                    expected_head=current.source_head,
                    target_root=Path(current.target_root),
                    expected_target_fingerprint=current.target_after_fingerprint,
                )
        if current.state is SessionHandoffState.TARGET_MATERIALIZED:
            active_worktree_id = (
                current.associated_worktree_id
                if current.target_mode is SessionExecutionMode.WORKTREE
                else None
            )
            session = self._repository.read_session(current.session_id)
            if session is None:
                raise ResourceNotFoundError("session not found")
            if not _session_has_handoff_binding(
                session,
                execution_mode=current.target_mode,
                worktree_id=active_worktree_id,
                associated_worktree_id=current.associated_worktree_id,
            ):
                if not _session_has_handoff_binding(
                    session,
                    execution_mode=current.source_mode,
                    worktree_id=(
                        current.associated_worktree_id
                        if current.source_mode is SessionExecutionMode.WORKTREE
                        else None
                    ),
                    associated_worktree_id=(
                        current.associated_worktree_id
                        if current.source_mode is SessionExecutionMode.WORKTREE
                        else session.associated_worktree_id
                    ),
                ):
                    raise WorktreeError("handoff_recovery_required")
                mutation = self._repository.update_execution_binding(
                    current.session_id,
                    execution_mode=current.target_mode,
                    worktree_id=active_worktree_id,
                    associated_worktree_id=current.associated_worktree_id,
                )
                session = mutation.value
            current = repository.update_state(
                current.scope,
                current.operation_id,
                SessionHandoffState.SESSION_REBOUND,
            )
        elif current.state is not SessionHandoffState.SESSION_REBOUND:
            session = self._repository.read_session(current.session_id)
            if session is None:
                raise ResourceNotFoundError("session not found")
        if current.state is SessionHandoffState.SESSION_REBOUND:
            session = self._repository.read_session(current.session_id)
            if session is None:
                raise ResourceNotFoundError("session not found")
            if session.execution_mode is not current.target_mode:
                raise WorktreeError("handoff_recovery_required")
            if current.target_mode is SessionExecutionMode.WORKTREE:
                touch = getattr(manager, "touch_last_used", None)
                if touch is not None:
                    touch(current.associated_worktree_id)
            repository.update_state(
                current.scope,
                current.operation_id,
                SessionHandoffState.COMPLETED,
            )
            if current.target_mode is SessionExecutionMode.WORKTREE and self._retention is not None:
                try:
                    self._retention.reconcile()
                except Exception:
                    self._logger.exception(
                        "Worktree retention reconciliation after handoff failed",
                        extra={"worktree_id": current.associated_worktree_id},
                    )
            self._activate_projection(
                self._projection_for_session(current.session_id)
            )
        return session

    def _materialize_worktree_target(
        self,
        operation: SessionHandoffOperation,
        source: GitSourceSnapshot,
    ) -> GitSourceSnapshot:
        manager = self._require_manager()
        target_worktree: Worktree | None = None
        try:
            target_worktree = manager.read_worktree(operation.associated_worktree_id)
        except WorktreeError:
            if not operation.target_worktree_new:
                raise WorktreeError("worktree_restore_required")
        if operation.target_worktree_new:
            if target_worktree is None:
                target_worktree = manager.create_prepared(
                    Worktree(
                        id=operation.associated_worktree_id,
                        project_id=operation.project_id,
                        worktree_root=operation.target_root,
                        git_dir=str(Path(operation.target_root) / ".git"),
                        base_ref=operation.target_base_ref or "HEAD",
                        base_commit=operation.target_base_commit or source.head,
                        branch=None,
                        checkout_branch=None,
                        ownership=WorktreeOwnership.MANAGED,
                        state=WorktreeState.ACTIVE,
                        created_at=operation.created_at,
                        updated_at=operation.updated_at,
                    ),
                    include_local_changes=True,
                    expected_source_head=operation.source_head,
                    expected_source_fingerprint=operation.source_fingerprint,
                )
            target = manager.source_snapshot(
                Path(operation.target_root), include_local_changes=True
            )
            if (
                target.head == source.head
                and target.branch is None
                and _same_transfer_state(source, target)
            ):
                return target
            if target_worktree is not None and not target.status.dirty:
                # A crash can happen after the Worktree row is committed but
                # before the dirty patch is applied.  The new Worktree still
                # has the prepared commit, so applying the same captured
                # patch is safe and keeps the retry idempotent.
                if source.changes is not None:
                    manager.apply_worktree_changes(
                        Path(operation.target_root), source.changes
                    )
                    target = manager.source_snapshot(
                        Path(operation.target_root), include_local_changes=True
                    )
            if not _same_transfer_state(source, target):
                raise WorktreeError("handoff_git_conflict")
            return target
        if target_worktree is None:
            raise WorktreeError("worktree_restore_required")
        target_before = manager.source_snapshot(
            Path(operation.target_root), include_local_changes=True
        )
        if _matches_handoff_transfer(operation, source, target_before):
            return target_before
        if target_before.fingerprint != operation.target_fingerprint:
            raise WorktreeError("handoff_target_changed")
        manager.move_worktree_to_head(
            target_worktree.id,
            expected_current_head=operation.target_head,
            target_head=source.head,
        )
        if source.changes is not None:
            manager.apply_worktree_changes(Path(operation.target_root), source.changes)
        target = manager.source_snapshot(
            Path(operation.target_root), include_local_changes=True
        )
        if not _same_transfer_state(source, target):
            raise WorktreeError("handoff_git_conflict")
        return target

    def _materialize_local_target(
        self,
        operation: SessionHandoffOperation,
        source: GitSourceSnapshot,
    ) -> GitSourceSnapshot:
        manager = self._require_manager()
        target_before = manager.source_snapshot(
            Path(operation.target_root), include_local_changes=True
        )
        if _matches_handoff_transfer(operation, source, target_before):
            return target_before
        if target_before.fingerprint != operation.target_fingerprint:
            raise WorktreeError("handoff_target_changed")
        if target_before.status.dirty:
            raise WorktreeError("handoff_local_conflict")
        manager.detach_worktree_for_handoff(
            operation.associated_worktree_id,
            expected_head=operation.source_head,
        )
        if source.branch is not None:
            manager.switch_repository_branch(Path(operation.target_root), source.branch)
        else:
            manager.switch_repository_detached(
                Path(operation.target_root), source.head
            )
        if source.changes is not None:
            manager.apply_worktree_changes(Path(operation.target_root), source.changes)
        target = manager.source_snapshot(
            Path(operation.target_root), include_local_changes=True
        )
        if target.head != source.head or target.branch != source.branch:
            raise WorktreeError("handoff_git_conflict")
        if not _same_transfer_state(source, target):
            raise WorktreeError("handoff_git_conflict")
        return target

    def _read_handoff_worktree(self, worktree_id: str) -> Worktree:
        try:
            worktree = self._require_manager().read_worktree(worktree_id)
            if worktree.state in {
                WorktreeState.MISSING,
                WorktreeState.DELETED,
            }:
                raise WorktreeError("worktree_restore_required")
            if worktree.state is WorktreeState.INVALID:
                raise WorktreeError("worktree_recovery_required")
            return worktree
        except WorktreeError as error:
            if error.code in {
                "worktree_restore_required",
                "worktree_recovery_required",
            }:
                raise
            if error.code in {"worktree_not_found", "worktree_missing"}:
                raise WorktreeError("worktree_restore_required") from error
            raise WorktreeError("worktree_recovery_required") from error

    def _assert_inactive_worktree(
        self,
        session_id: str,
        worktree_id: str,
        snapshot: GitSourceSnapshot,
    ) -> None:
        latest = self._store.session_handoff_repository().latest_for_session(
            session_id
        )
        if latest is None or latest.associated_worktree_id != worktree_id:
            return
        if latest.source_after_fingerprint is not None:
            if snapshot.fingerprint != latest.source_after_fingerprint:
                raise WorktreeError("handoff_target_changed")

    def _mark_handoff_failure(
        self,
        operation: SessionHandoffOperation | None,
        error: Exception,
    ) -> None:
        if operation is None or operation.state in {
            SessionHandoffState.COMPLETED,
            SessionHandoffState.CLEANUP_REQUIRED,
        }:
            return
        try:
            self._store.session_handoff_repository().update_state(
                operation.scope,
                operation.operation_id,
                SessionHandoffState.CLEANUP_REQUIRED,
                error_code=_handoff_exception_code(error),
            )
        except Exception:
            self._logger.exception(
                "session handoff failure could not persist cleanup state",
                extra={
                    "session_id": operation.session_id,
                    "operation_id": operation.operation_id,
                },
            )

    def git_context(self, request: GitContextRequestDto) -> GitContextResponseDto:
        manager = self._worktree_manager
        if manager is None:
            return _result(
                GitContextResponseDto,
                GitRepositoryContext(
                    git_available=False,
                    current_branch=None,
                    head=None,
                    branches=(),
                    dirty=False,
                    changed_file_count=0,
                ).model_dump(mode="json"),
            )
        resolution = self._resolve_project(request.workspace_root)
        if resolution.git is None:
            return _result(
                GitContextResponseDto,
                {
                    "gitAvailable": False,
                    "currentBranch": None,
                    "head": None,
                    "branches": [],
                    "dirty": False,
                    "changedFileCount": 0,
                },
            )
        repository_root = Path(resolution.git.repository_root)
        try:
            source_status = manager.source_snapshot(
                repository_root,
                include_local_changes=False,
            ).status
            context = GitRepositoryContext(
                git_available=True,
                current_branch=manager.current_branch(repository_root),
                head=manager.head(repository_root),
                branches=manager.local_branches(repository_root),
                dirty=source_status.dirty,
                changed_file_count=len(
                    set(source_status.staged_paths)
                    | set(source_status.unstaged_paths)
                    | set(source_status.untracked_paths)
                    | set(source_status.conflict_paths)
                ),
            )
        except WorktreeError as error:
            raise ApplicationError(_worktree_error_code(error), str(error)) from error
        return _result(
            GitContextResponseDto,
            {
                "gitAvailable": context.git_available,
                "currentBranch": context.current_branch,
                "head": context.head,
                "branches": list(context.branches),
                "dirty": context.dirty,
                "changedFileCount": context.changed_file_count,
            },
        )

    def rename(self, request: SessionRenameRequestDto) -> SessionRenameResponseDto:
        title = clean_session_title(request.title)
        if not title:
            raise ApplicationError("INVALID_SESSION_TITLE", "session title is empty")
        try:
            title = clean_session_title(self._scan_text(title))
            mutation = self._repository.rename_session(
                request.session_id,
                title,
                operation_id=request.operation_id,
            )
        except SensitiveContentDenied as error:
            raise ApplicationError("SENSITIVE_CONTENT_REJECTED", str(error)) from error
        except SensitiveScanError as error:
            raise ApplicationError("SENSITIVE_SCAN_FAILED", str(error)) from error
        except ResourceNotFoundError as error:
            raise ApplicationError("RESOURCE_NOT_FOUND", str(error)) from error
        except OperationConflictError as error:
            raise ApplicationError("OPERATION_ID_REUSED", str(error)) from error
        except OperationInProgressError as error:
            raise ApplicationError("OPERATION_IN_PROGRESS", str(error)) from error
        except ValueError as error:
            raise ApplicationError("INVALID_SESSION_TITLE", str(error)) from error
        return _result(
            SessionRenameResponseDto,
            self._project_session(self._projection_for_session(mutation.value.id)),
        )

    def delete(self, request: SessionDeleteRequestDto) -> SessionDeleteResponseDto:
        operation_request = {"sessionId": request.session_id}
        operation_guard = (
            self._lifecycle.hold_operation(
                "session/delete", request.operation_id
            )
            if request.operation_id is not None
            else nullcontext()
        )
        try:
            with operation_guard:
                if request.operation_id is not None:
                    replay = self._store.operation_result(
                        request.operation_id,
                        "session/delete",
                        operation_request,
                    )
                    if replay is not None:
                        return _result(SessionDeleteResponseDto, replay)
                with self._lifecycle.hold(request.session_id):
                    manager = self._worktree_manager
                    lifecycle_operation = (
                        manager.lifecycle.read(
                            WorktreeLifecycleScope.SESSION_DELETE,
                            request.operation_id,
                        )
                        if manager is not None and request.operation_id is not None
                        else (
                            manager.lifecycle.find_delete_for_session(
                                request.session_id
                            )
                            if manager is not None
                            else None
                        )
                    )
                    if (
                        lifecycle_operation is not None
                        and lifecycle_operation.session_id != request.session_id
                    ):
                        raise ApplicationError(
                            "OPERATION_ID_REUSED",
                            "session delete operation identity changed",
                        )
                    if (
                        lifecycle_operation is not None
                        and lifecycle_operation.worktree_id is not None
                    ):
                        lifecycle_projection = self._repository.read_session_projection(
                            request.session_id
                        )
                        lifecycle_worktree_id = (
                            lifecycle_projection.worktree.worktree_id
                            if lifecycle_projection is not None
                            and lifecycle_projection.worktree is not None
                            else (
                                lifecycle_projection.session.associated_worktree_id
                                if lifecycle_projection is not None
                                else None
                            )
                        )
                        if (
                            lifecycle_worktree_id is not None
                            and lifecycle_operation.worktree_id != lifecycle_worktree_id
                        ):
                            raise ApplicationError(
                                "WORKTREE_RECOVERY_REQUIRED",
                                "session delete lifecycle Worktree identity changed",
                            )
                    if (
                        lifecycle_operation is not None
                        and lifecycle_operation.state
                        is WorktreeLifecycleState.COMPLETED
                        and request.operation_id is not None
                    ):
                        result = {"deletedSessionId": request.session_id}
                        return self._record_delete_operation(
                            request.operation_id,
                            operation_request,
                            result,
                        )
                    if lifecycle_operation is not None and lifecycle_operation.state in {
                        WorktreeLifecycleState.WORKTREE_DELETED,
                        WorktreeLifecycleState.COMPLETED,
                    } and self._repository.read_session_projection(
                        request.session_id
                    ) is None:
                        if lifecycle_operation.state is WorktreeLifecycleState.WORKTREE_DELETED:
                            manager = self._worktree_manager
                            assert manager is not None
                            manager.lifecycle.update_state(
                                WorktreeLifecycleScope.SESSION_DELETE,
                                lifecycle_operation.operation_id,
                                WorktreeLifecycleState.COMPLETED,
                            )
                        result = {"deletedSessionId": request.session_id}
                        if request.operation_id is not None:
                            return self._record_delete_operation(
                                request.operation_id,
                                operation_request,
                                result,
                            )
                        return _result(SessionDeleteResponseDto, result)
                    projection = self._repository.read_session_projection(
                        request.session_id
                    )
                    if projection is None:
                        raise ResourceNotFoundError("session not found")
                    self._repository.assert_session_deletable(request.session_id)
                    worktree_projection = projection.worktree
                    if worktree_projection is None and projection.session.associated_worktree_id:
                        if manager is None:
                            raise ApplicationError(
                                "INTERNAL_ERROR",
                                "associated Session Worktree boundary is unavailable",
                            )
                        associated = manager.read_worktree(
                            projection.session.associated_worktree_id
                        )
                        worktree_projection = SessionWorktreeProjection(
                            worktree_id=associated.id,
                            project_id=associated.project_id,
                            repository_root=manager.project(
                                associated.project_id
                            ).workspace_root,
                            worktree_root=associated.worktree_root,
                            base_ref=associated.base_ref,
                            base_commit=associated.base_commit,
                            branch=associated.checkout_branch,
                            state=associated.state,
                        )
                    if (
                        worktree_projection is not None
                        and worktree_projection.state.value == "deleted"
                        and self._retention is not None
                        and self._retention.has_ready_snapshot(
                            worktree_projection.worktree_id
                        )
                    ):
                        if lifecycle_operation is None:
                            snapshot_id = self._retention.latest_ready_snapshot_id(
                                worktree_projection.worktree_id
                            )
                            if snapshot_id is None:
                                raise ApplicationError(
                                    "WORKTREE_RESTORE_REQUIRED",
                                    "deleted Worktree snapshot is no longer ready",
                                )
                            project = manager.project(worktree_projection.project_id)
                            lifecycle_operation_id = request.operation_id or (
                                f"session-delete-{uuid.uuid4().hex}"
                            )
                            now = datetime.now(UTC).replace(microsecond=0)
                            lifecycle_operation = manager.lifecycle.prepare(
                                WorktreeLifecycleOperation(
                                    scope=WorktreeLifecycleScope.SESSION_DELETE,
                                    operation_id=lifecycle_operation_id,
                                    state=WorktreeLifecycleState.PREPARED,
                                    project_id=project.id,
                                    repository_root=project.workspace_root,
                                    worktree_id=worktree_projection.worktree_id,
                                    worktree_root=worktree_projection.worktree_root,
                                    base_ref=worktree_projection.base_ref,
                                    branch=worktree_projection.branch,
                                    base_commit=worktree_projection.base_commit,
                                    session_id=request.session_id,
                                    snapshot_id=snapshot_id,
                                    created_at=now,
                                    updated_at=now,
                                )
                            )
                        if lifecycle_operation.state is WorktreeLifecycleState.CLEANUP_REQUIRED:
                            raise ApplicationError(
                                "WORKTREE_RECOVERY_REQUIRED",
                                lifecycle_operation.error_code
                                or "Session snapshot cleanup recovery is required",
                            )
                        self._retention.delete_snapshots_for_worktree(
                            worktree_projection.worktree_id
                        )
                        if lifecycle_operation.state is WorktreeLifecycleState.PREPARED:
                            lifecycle_operation = manager.lifecycle.update_state(
                                WorktreeLifecycleScope.SESSION_DELETE,
                                lifecycle_operation.operation_id,
                                WorktreeLifecycleState.WORKTREE_DELETED,
                            )
                        mutation = self._repository.delete_session(
                            request.session_id,
                            operation_id=None,
                        )
                        manager.lifecycle.update_state(
                            WorktreeLifecycleScope.SESSION_DELETE,
                            lifecycle_operation.operation_id,
                            WorktreeLifecycleState.COMPLETED,
                        )
                        result = deleted_session_to_legacy_dict(mutation.value)
                        if request.operation_id is not None:
                            return self._record_delete_operation(
                                request.operation_id,
                                operation_request,
                                result,
                            )
                        return _result(SessionDeleteResponseDto, result)
                    if worktree_projection is not None:
                        if manager is None:
                            raise ApplicationError(
                                "INTERNAL_ERROR",
                                "managed Session has no Worktree application boundary",
                            )
                        if lifecycle_operation is None:
                            if worktree_projection.state.value == "deleted":
                                raise ApplicationError(
                                    "WORKTREE_RECOVERY_REQUIRED",
                                    "deleted Worktree has no durable Session delete intent",
                                )
                            project = manager.project(worktree_projection.project_id)
                            now = datetime.now(UTC).replace(microsecond=0)
                            lifecycle_operation_id = request.operation_id or (
                                f"session-delete-{uuid.uuid4().hex}"
                            )
                            lifecycle_operation = manager.lifecycle.prepare(
                                WorktreeLifecycleOperation(
                                    scope=WorktreeLifecycleScope.SESSION_DELETE,
                                    operation_id=lifecycle_operation_id,
                                    state=WorktreeLifecycleState.PREPARED,
                                    project_id=project.id,
                                    repository_root=project.workspace_root,
                                    worktree_id=worktree_projection.worktree_id,
                                    worktree_root=worktree_projection.worktree_root,
                                    base_ref=worktree_projection.base_ref,
                                    branch=worktree_projection.branch,
                                    base_commit=worktree_projection.base_commit,
                                    session_id=request.session_id,
                                    created_at=now,
                                    updated_at=now,
                                )
                            )
                        if lifecycle_operation.state is WorktreeLifecycleState.CLEANUP_REQUIRED:
                            raise ApplicationError(
                                "WORKTREE_RECOVERY_REQUIRED",
                                lifecycle_operation.error_code
                                or "Session delete recovery is required",
                            )
                        if lifecycle_operation.state in {
                            WorktreeLifecycleState.PREPARED,
                            WorktreeLifecycleState.WORKTREE_DELETED,
                        }:
                            manager.delete(worktree_projection.worktree_id)
                            if lifecycle_operation.state is not WorktreeLifecycleState.WORKTREE_DELETED:
                                lifecycle_operation = manager.lifecycle.update_state(
                                    WorktreeLifecycleScope.SESSION_DELETE,
                                    lifecycle_operation.operation_id,
                                    WorktreeLifecycleState.WORKTREE_DELETED,
                                )
                        elif lifecycle_operation.state is not WorktreeLifecycleState.WORKTREE_DELETED and (
                            worktree_projection.state.value != "deleted"
                        ):
                            manager.delete(worktree_projection.worktree_id)
                            lifecycle_operation = manager.lifecycle.update_state(
                                WorktreeLifecycleScope.SESSION_DELETE,
                                lifecycle_operation.operation_id,
                                WorktreeLifecycleState.WORKTREE_DELETED,
                            )
                        mutation = self._repository.delete_session(
                            request.session_id,
                            operation_id=None,
                        )
                        manager.lifecycle.update_state(
                            WorktreeLifecycleScope.SESSION_DELETE,
                            lifecycle_operation.operation_id,
                            WorktreeLifecycleState.COMPLETED,
                        )
                        result = deleted_session_to_legacy_dict(mutation.value)
                        if request.operation_id is not None:
                            return self._record_delete_operation(
                                request.operation_id,
                                operation_request,
                                result,
                            )
                    else:
                        mutation = self._repository.delete_session(
                            request.session_id,
                            operation_id=request.operation_id,
                        )
        except ResourceNotFoundError as error:
            raise ApplicationError("RESOURCE_NOT_FOUND", str(error)) from error
        except SessionActiveError as error:
            raise ApplicationError("SESSION_HAS_ACTIVE_RUN", str(error)) from error
        except WorktreeError as error:
            raise ApplicationError(
                _managed_delete_error_code(error), str(error)
            ) from error
        except StorageError as error:
            raise ApplicationError(
                "SESSION_PERSISTENCE_FAILED", str(error)
            ) from error
        except OperationConflictError as error:
            raise ApplicationError("OPERATION_ID_REUSED", str(error)) from error
        except OperationInProgressError as error:
            raise ApplicationError("OPERATION_IN_PROGRESS", str(error)) from error
        return _result(
            SessionDeleteResponseDto,
            deleted_session_to_legacy_dict(mutation.value),
        )

    def _record_delete_operation(
        self,
        operation_id: str,
        operation_request: dict[str, object],
        result: dict[str, object],
    ) -> SessionDeleteResponseDto:
        try:
            recorded = self._store.record_operation_result(
                operation_id,
                "session/delete",
                operation_request,
                result,
            )
        except OperationConflictError as error:
            raise ApplicationError("OPERATION_ID_REUSED", str(error)) from error
        except OperationInProgressError as error:
            raise ApplicationError("OPERATION_IN_PROGRESS", str(error)) from error
        return _result(SessionDeleteResponseDto, recorded)

    def list_events(self, request: EventListRequestDto) -> EventListResponseDto:
        try:
            events = self._store.list_events(
                request.session_id,
                after_event_id=request.after_event_id,
                limit=request.limit,
            )
        except ValueError as error:
            raise ApplicationInvalidParamsError("INVALID_EVENT_CURSOR", str(error)) from error
        return _result(EventListResponseDto, events)

    def _project_session(self, projection: SessionProjection) -> dict[str, object]:
        session = projection.session
        value = session_to_legacy_dict(session)
        value["executionMode"] = session.execution_mode.value
        if session.associated_worktree_id is not None:
            value["associatedWorktreeId"] = session.associated_worktree_id
            value["worktreeRestoreAvailable"] = bool(
                self._retention is not None
                and self._retention.has_ready_snapshot(
                    session.associated_worktree_id
                )
                and (
                    projection.worktree is None
                    or projection.worktree.state.value == "deleted"
                )
            )
        value["project"] = SessionProjectDto(
            id=projection.project.id,
            workspaceRoot=projection.project.workspace_root,
            gitAvailable=projection.project.git_available,
        )
        if projection.worktree is not None:
            worktree = projection.worktree
            value["worktree"] = SessionWorktreeDto(
                worktreeId=worktree.worktree_id,
                projectId=worktree.project_id,
                repositoryRoot=worktree.repository_root,
                worktreeRoot=worktree.worktree_root,
                baseRef=worktree.base_ref,
                baseCommit=worktree.base_commit,
                branch=worktree.branch,
                state=worktree.state.value,
            )
        projected = SessionDto.model_validate(value).to_json_value()
        if projection.worktree is not None and projection.worktree.branch is None:
            worktree_value = projected.setdefault("worktree", {})
            if isinstance(worktree_value, dict):
                worktree_value["branch"] = None
        projected["executionMode"] = session.execution_mode.value
        return projected

    def _projection_for_session(self, session_id: str) -> SessionProjection:
        projection = self._repository.read_session_projection(session_id)
        if projection is None:
            raise ApplicationError(
                "INTERNAL_ERROR", "committed Session projection is unavailable"
            )
        return projection

    def _managed_binding(
        self, session_id: str
    ) -> tuple[ManagedWorktreePort, str]:
        session = self._repository.read_session(session_id)
        if session is None:
            raise ApplicationError("RESOURCE_NOT_FOUND", "session not found")
        if session.worktree_id is None:
            raise ApplicationError(
                "GIT_WORKTREE_NOT_MANAGED", "session has no managed Worktree"
            )
        manager = self._worktree_manager
        if manager is None:
            raise ApplicationError(
                "INTERNAL_ERROR", "managed Session has no Worktree application boundary"
            )
        return manager, session.worktree_id

    def _require_manager(self) -> ManagedWorktreePort:
        manager = self._worktree_manager
        if manager is None:
            raise ApplicationError(
                "INTERNAL_ERROR", "Session Git application boundary is unavailable"
            )
        return manager


def _result(result_type: type[ResultT], value: object) -> ResultT:
    try:
        return result_type.model_validate(value)
    except ValidationError as error:
        raise ApplicationError(
            "INTERNAL_ERROR", "stored Session result violates its protocol contract"
        ) from error


__all__ = [
    "DeferredGitFetch",
    "DeferredGitPull",
    "DeferredGitPush",
    "MAX_SESSION_TITLE_BYTES",
    "SessionApplication",
    "ManagedWorktreePort",
    "SessionStorePort",
    "TypedSessionRepositoryPort",
    "clean_session_title",
]


def _worktree_error_code(error: WorktreeError) -> str:
    return {
        "repository_not_found": "REPOSITORY_NOT_FOUND",
        "not_a_git_repository": "NOT_A_GIT_REPOSITORY",
        "git_command_timeout": "GIT_COMMAND_TIMEOUT",
        "base_ref_not_found": "BASE_REF_NOT_FOUND",
        "worktree_persistence_failed": "WORKTREE_PERSISTENCE_FAILED",
        "local_changes_base_mismatch": "LOCAL_CHANGES_BASE_MISMATCH",
        "worktree_source_changed": "WORKTREE_SOURCE_CHANGED",
        "worktree_local_changes_conflict": "WORKTREE_LOCAL_CHANGES_CONFLICT",
        "worktree_include_invalid": "WORKTREE_INCLUDE_INVALID",
        "worktree_include_symlink_escape": "WORKTREE_INCLUDE_INVALID",
        "worktree_include_copy_failed": "WORKTREE_INCLUDE_FAILED",
        "worktree_include_source_invalid": "WORKTREE_INCLUDE_INVALID",
        "worktree_include_target_invalid": "WORKTREE_INCLUDE_FAILED",
        "worktree_cleanup_required": "WORKTREE_RECOVERY_REQUIRED",
        "worktree_snapshot_anchor_mismatch": "WORKTREE_RESTORE_REQUIRED",
        "worktree_snapshot_anchor_changed": "WORKTREE_RESTORE_REQUIRED",
        "worktree_snapshot_anchor_unavailable": "WORKTREE_RESTORE_REQUIRED",
        "worktree_snapshot_checksum_mismatch": "WORKTREE_RESTORE_REQUIRED",
        "worktree_restore_failed": "WORKTREE_RESTORE_REQUIRED",
        "worktree_restore_verification_failed": "WORKTREE_RESTORE_REQUIRED",
        "worktree_snapshot_required": "WORKTREE_RESTORE_REQUIRED",
        "worktree_restore_not_required": "WORKTREE_RESTORE_REQUIRED",
        "worktree_snapshot_cleanup_required": "WORKTREE_RECOVERY_REQUIRED",
    }.get(error.code, "WORKTREE_CREATE_FAILED")


def _branch_error_code(error: WorktreeError) -> str:
    return {
        "worktree_required": "WORKTREE_REQUIRED",
        "worktree_not_found": "WORKTREE_NOT_FOUND",
        "worktree_invalid": "WORKTREE_INVALID",
        "worktree_already_attached": "WORKTREE_ALREADY_ATTACHED",
        "branch_already_exists": "BRANCH_ALREADY_EXISTS",
        "worktree_branch_in_use": "WORKTREE_BRANCH_IN_USE",
        "worktree_branch_invalid": "BRANCH_INVALID",
        "worktree_state_changed": "WORKTREE_BRANCH_STATE_CHANGED",
        "worktree_branch_state_changed": "WORKTREE_BRANCH_STATE_CHANGED",
        "worktree_branch_attach_failed": "WORKTREE_BRANCH_CREATE_FAILED",
        "worktree_branch_recovery_required": "WORKTREE_RECOVERY_REQUIRED",
        "git_command_timeout": "GIT_COMMAND_TIMEOUT",
        "git_command_failed": "WORKTREE_BRANCH_CREATE_FAILED",
    }.get(error.code, "WORKTREE_BRANCH_CREATE_FAILED")


def _workspace_resolution_error_code(error: WorktreeError) -> str:
    return {
        "repository_not_found": "REPOSITORY_NOT_FOUND",
        "workspace_not_found": "REPOSITORY_NOT_FOUND",
        "workspace_not_directory": "REPOSITORY_NOT_FOUND",
        "workspace_symlink": "WORKSPACE_BOUNDARY_VIOLATION",
        "workspace_identity_unavailable": "WORKSPACE_IDENTITY_UNAVAILABLE",
    }.get(error.code, _worktree_error_code(error))


def _git_review_error_code(error: WorktreeError) -> str:
    if error.code == "not_a_git_repository":
        return "GIT_NOT_REPOSITORY"
    if error.code in {
        "git_command_failed",
        "git_command_timeout",
        "git_observation_failed",
        "git_observation_incomplete",
    }:
        return "GIT_OBSERVATION_UNAVAILABLE"
    if error.code == "worktree_not_found":
        return "GIT_WORKTREE_NOT_FOUND"
    if error.code == "worktree_missing":
        return "GIT_WORKTREE_MISSING"
    if error.code == "worktree_snapshot_cleanup_required":
        return "WORKTREE_RECOVERY_REQUIRED"
    if error.code in {"worktree_remove_failed", "worktree_persistence_failed"}:
        return "WORKTREE_DELETE_FAILED"
    if error.code.startswith("worktree_"):
        return "GIT_WORKTREE_INVALID"
    return "GIT_REVIEW_FAILED"


def _managed_delete_error_code(error: WorktreeError) -> str:
    if error.code == "worktree_dirty":
        return "WORKTREE_DIRTY"
    if error.code in {
        "git_command_failed",
        "git_command_timeout",
        "git_observation_failed",
        "git_observation_incomplete",
    }:
        return "GIT_OBSERVATION_UNAVAILABLE"
    if error.code == "worktree_not_found":
        return "GIT_WORKTREE_NOT_FOUND"
    if error.code == "worktree_missing":
        return "GIT_WORKTREE_MISSING"
    if error.code in {"worktree_remove_failed", "worktree_persistence_failed"}:
        return "WORKTREE_DELETE_FAILED"
    if error.code.startswith("worktree_"):
        return "GIT_WORKTREE_INVALID"
    return "WORKTREE_DELETE_FAILED"


def _timestamp_millis(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _handoff_timestamp() -> datetime:
    now = datetime.now(UTC)
    return datetime.fromtimestamp(
        int(now.timestamp() * 1000) / 1000,
        tz=UTC,
    )


def _planned_target_fingerprint(worktree_id: str) -> str:
    return hashlib.sha256(
        f"planned-worktree:{worktree_id}".encode("utf-8")
    ).hexdigest()


def _same_transfer_state(
    source: GitSourceSnapshot, target: GitSourceSnapshot
) -> bool:
    return (
        source.head == target.head
        and source.status.staged_paths == target.status.staged_paths
        and source.status.unstaged_paths == target.status.unstaged_paths
        and source.status.untracked_paths == target.status.untracked_paths
        and source.status.conflict_paths == target.status.conflict_paths
        and source.changes == target.changes
    )


def _matches_handoff_transfer(
    operation: SessionHandoffOperation,
    source: GitSourceSnapshot,
    target: GitSourceSnapshot,
) -> bool:
    """Recognize a completed Git transfer before its state row was advanced."""

    expected_target_branch = (
        None
        if operation.target_mode is SessionExecutionMode.WORKTREE
        else operation.source_branch
    )
    source_branch_is_valid = (
        source.branch == operation.source_branch
        if operation.source_mode is SessionExecutionMode.LOCAL
        else source.branch in {operation.source_branch, None}
    )
    return (
        source_branch_is_valid
        and source.head == operation.source_head
        and target.head == operation.source_head
        and target.branch == expected_target_branch
        and _same_transfer_state(source, target)
    )


def _session_has_handoff_binding(
    session: Session,
    *,
    execution_mode: SessionExecutionMode,
    worktree_id: str | None,
    associated_worktree_id: str | None,
) -> bool:
    return (
        session.execution_mode is execution_mode
        and session.worktree_id == worktree_id
        and session.associated_worktree_id == associated_worktree_id
    )


def _handoff_exception_code(error: Exception) -> str:
    if isinstance(error, WorktreeError):
        return error.code
    if isinstance(error, SessionActiveError):
        return "session_has_active_run"
    if isinstance(error, ResourceNotFoundError):
        return "resource_not_found"
    if isinstance(error, StorageError):
        return "session_handoff_storage_failed"
    return "session_handoff_recovery_failed"


def _handoff_error_code(error: str | None) -> str:
    return {
        "handoff_not_supported": "HANDOFF_NOT_SUPPORTED",
        "handoff_source_changed": "HANDOFF_SOURCE_CHANGED",
        "handoff_target_changed": "HANDOFF_TARGET_CHANGED",
        "handoff_local_conflict": "HANDOFF_LOCAL_CONFLICT",
        "handoff_git_conflict": "HANDOFF_GIT_CONFLICT",
        "worktree_restore_required": "WORKTREE_RESTORE_REQUIRED",
        "worktree_recovery_required": "WORKTREE_RECOVERY_REQUIRED",
        "handoff_recovery_required": "WORKTREE_RECOVERY_REQUIRED",
        "worktree_not_found": "WORKTREE_RESTORE_REQUIRED",
        "worktree_missing": "WORKTREE_RESTORE_REQUIRED",
        "worktree_deleted": "WORKTREE_RESTORE_REQUIRED",
        "worktree_invalid": "WORKTREE_RECOVERY_REQUIRED",
        "worktree_cleanup_required": "WORKTREE_RECOVERY_REQUIRED",
        "worktree_snapshot_anchor_mismatch": "WORKTREE_RESTORE_REQUIRED",
        "worktree_snapshot_anchor_changed": "WORKTREE_RESTORE_REQUIRED",
        "worktree_snapshot_anchor_unavailable": "WORKTREE_RESTORE_REQUIRED",
        "worktree_snapshot_checksum_mismatch": "WORKTREE_RESTORE_REQUIRED",
        "worktree_restore_failed": "WORKTREE_RESTORE_REQUIRED",
        "worktree_restore_verification_failed": "WORKTREE_RESTORE_REQUIRED",
        "worktree_snapshot_required": "WORKTREE_RESTORE_REQUIRED",
        "worktree_restore_not_required": "WORKTREE_RESTORE_REQUIRED",
        "git_command_timeout": "HANDOFF_GIT_CONFLICT",
        "session_has_active_run": "SESSION_HAS_ACTIVE_RUN",
        "resource_not_found": "RESOURCE_NOT_FOUND",
        "session_handoff_storage_failed": "WORKTREE_RECOVERY_REQUIRED",
        "session_handoff_recovery_failed": "WORKTREE_RECOVERY_REQUIRED",
    }.get(error or "", "HANDOFF_GIT_CONFLICT")
