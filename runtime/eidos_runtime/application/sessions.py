from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from datetime import UTC, datetime
import logging
from pathlib import Path
import unicodedata
import uuid
from typing import Protocol, TypeVar

from pydantic import ValidationError

from eidos_runtime.application.errors import (
    ApplicationError,
    ApplicationInvalidParamsError,
)
from eidos_runtime.application.session_lifecycle import SessionLifecycleCoordinator
from eidos_runtime.db.database import CommittedMutation
from eidos_runtime.db.errors import (
    InvalidCursorError,
    OperationConflictError,
    OperationInProgressError,
    ResourceNotFoundError,
    SessionActiveError,
    StorageError,
    WorkspaceBoundaryError,
)
from eidos_runtime.domain.project import Project
from eidos_runtime.domain.worktree import (
    Worktree,
    WorktreeLifecycleOperation,
    WorktreeLifecycleScope,
    WorktreeLifecycleState,
)
from eidos_runtime.git.errors import WorktreeError
from eidos_runtime.git.models import GitRepositoryDiscovery
from eidos_runtime.git.status import DiffScope, GitDiffSnapshot, GitStatusSnapshot
from eidos_runtime.protocol.methods import (
    EventListRequestDto,
    EventListResponseDto,
    MethodResultDto,
    SessionCreateRequestDto,
    SessionCreateResponseDto,
    SessionDeleteRequestDto,
    SessionDeleteResponseDto,
    SessionGitDiffRequestDto,
    SessionGitDiffResponseDto,
    SessionGitStatusRequestDto,
    SessionGitStatusResponseDto,
    SessionListRequestDto,
    SessionListResponseDto,
    SessionReadRequestDto,
    SessionReadResponseDto,
    SessionRenameRequestDto,
    SessionRenameResponseDto,
)
from eidos_runtime.domain.session import (
    DeletedSession,
    Session,
    SessionPage,
    SessionProjection,
    SessionProjectionPage,
)
from eidos_runtime.persistence.mappers.session import (
    deleted_session_to_legacy_dict,
    session_from_legacy_dict,
    session_to_legacy_dict,
)
from eidos_runtime.persistence.worktree_lifecycle import WorktreeLifecycleRepository
from eidos_runtime.protocol.schemas import SessionDto, SessionWorktreeDto
from eidos_runtime.sandbox.sensitive import (
    SensitiveContentDenied,
    SensitiveScanError,
)


MAX_SESSION_TITLE_BYTES = 120
ResultT = TypeVar("ResultT", bound=MethodResultDto)


class TypedSessionRepositoryPort(Protocol):
    """Typed Session reads/writes consumed by the application layer."""

    def create_session(
        self,
        workspace_root: str,
        *,
        worktree_id: str | None = None,
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

    def record_operation_result(
        self,
        operation_id: str,
        scope: str,
        request: dict[str, object],
        result: dict[str, object],
    ) -> dict[str, object]: ...

class ManagedWorktreePort(Protocol):
    """Application port for Session-owned Worktree provisioning."""

    def discover(self, repository_seed: Path | str) -> GitRepositoryDiscovery: ...

    def create(self, repository_root: Path | str) -> Worktree: ...

    def prepare_create(
        self, repository_root: Path | str
    ) -> Worktree: ...

    def create_prepared(
        self,
        plan: Worktree,
        *,
        compensate_on_failure: bool = True,
    ) -> Worktree: ...

    def prepared_from_lifecycle(
        self, operation: WorktreeLifecycleOperation
    ) -> Worktree: ...

    def project(self, project_id: str) -> Project: ...

    def status(self, worktree_id: str) -> GitStatusSnapshot: ...

    def diff(
        self, worktree_id: str, *, scope: DiffScope = DiffScope.HEAD
    ) -> GitDiffSnapshot: ...

    def delete(self, worktree_id: str) -> Worktree: ...

    def rollback_create(self, worktree_id: str) -> Worktree: ...

    @property
    def lifecycle(self) -> WorktreeLifecycleRepository: ...


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
    ) -> None:
        self._store = store
        self._repository: TypedSessionRepositoryPort = (
            store.typed_runtime_repository()
        )
        self._scan_text = scan_text
        self._worktree_manager = worktree_manager
        self._lifecycle = lifecycle or SessionLifecycleCoordinator()
        self._logger = logging.getLogger(__name__)

    def create(self, request: SessionCreateRequestDto) -> SessionCreateResponseDto:
        if self._worktree_manager is not None:
            operation_guard = (
                self._lifecycle.hold_operation(
                    "session/create", request.operation_id
                )
                if request.operation_id is not None
                else nullcontext()
            )
            with operation_guard:
                return self._create_managed(request)
        try:
            mutation = self._repository.create_session(
                request.workspace_root,
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
        return _result(
            SessionCreateResponseDto,
            self._project_session(self._projection_for_session(mutation.value.id)),
        )

    def _create_managed(
        self, request: SessionCreateRequestDto
    ) -> SessionCreateResponseDto:
        manager = self._worktree_manager
        assert manager is not None
        try:
            seed = Path(request.workspace_root)
            if seed.is_symlink():
                raise WorkspaceBoundaryError("workspace root is a symlink")
            discovery = manager.discover(seed)
        except WorkspaceBoundaryError as error:
            raise ApplicationError(
                "WORKSPACE_BOUNDARY_VIOLATION", str(error)
            ) from error
        except WorktreeError as error:
            raise ApplicationError(_worktree_error_code(error), str(error)) from error

        operation_request = {"workspaceRoot": discovery.repository_root}
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
                plan = manager.prepare_create(discovery.repository_root)
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
                        created_at=now,
                        updated_at=now,
                    )
                )
            elif lifecycle_operation.repository_root != discovery.repository_root:
                raise ApplicationError(
                    "OPERATION_ID_REUSED",
                    "operation id was reused for another repository",
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
                compensate_on_failure=False,
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
                        project.repository_root,
                        worktree_id=worktree.id,
                        operation_id=None,
                        session_id=session_id,
                    )
                except TypeError as error:
                    if "session_id" not in str(error):
                        raise
                    # Keep narrow compatibility with older injected repository
                    # seams used by failure tests.
                    mutation = self._repository.create_session(
                        project.repository_root,
                        worktree_id=worktree.id,
                        operation_id=None,
                    )
                session = mutation.value
            else:
                if existing_session.worktree_id != worktree.id:
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
        manager, worktree_id = self._managed_binding(request.session_id)
        try:
            status = manager.status(worktree_id)
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
                "observedAt": _timestamp_millis(status.observed_at),
            },
        )

    def git_diff(
        self, request: SessionGitDiffRequestDto
    ) -> SessionGitDiffResponseDto:
        manager, worktree_id = self._managed_binding(request.session_id)
        try:
            diff = manager.diff(worktree_id, scope=DiffScope(request.scope))
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
                "truncated": diff.truncated,
                "observedAt": _timestamp_millis(diff.observed_at),
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
                        if (
                            lifecycle_projection is not None
                            and lifecycle_projection.worktree is not None
                            and lifecycle_operation.worktree_id
                            != lifecycle_projection.worktree.worktree_id
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
                    if projection.worktree is not None:
                        if manager is None:
                            raise ApplicationError(
                                "INTERNAL_ERROR",
                                "managed Session has no Worktree application boundary",
                            )
                        worktree_projection = projection.worktree
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
                                    repository_root=project.repository_root,
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
        return SessionDto.model_validate(value).to_json_value()

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


def _result(result_type: type[ResultT], value: object) -> ResultT:
    try:
        return result_type.model_validate(value)
    except ValidationError as error:
        raise ApplicationError(
            "INTERNAL_ERROR", "stored Session result violates its protocol contract"
        ) from error


__all__ = [
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
        "worktree_persistence_failed": "WORKTREE_PERSISTENCE_FAILED",
    }.get(error.code, "WORKTREE_CREATE_FAILED")


def _git_review_error_code(error: WorktreeError) -> str:
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
