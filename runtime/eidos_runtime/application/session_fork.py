from __future__ import annotations

import uuid
import sqlite3
import threading
from pydantic import ValidationError
from datetime import UTC, datetime
from pathlib import Path

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.session_lifecycle import SessionLifecycleCoordinator
from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.application.worktree_snapshots import WorktreeSnapshotService
from eidos_runtime.db.database import canonical_hash
from eidos_runtime.db.errors import (
    InvalidRunStateError,
    OperationConflictError,
    OperationInProgressError,
    ResourceNotFoundError,
    StorageError,
)
from eidos_runtime.db.repositories.sessions import SessionRepository
from eidos_runtime.domain.worktree import (
    WorktreeLifecycleOperation,
    WorktreeLifecycleScope,
    WorktreeLifecycleState,
)
from eidos_runtime.git.errors import WorktreeError
from eidos_runtime.git.manager import WorktreeManager
from eidos_runtime.persistence.session_history import (
    SessionHistoryRepository,
    HistoryCaptureCancelled,
    HistoryLimitExceeded,
)
from eidos_runtime.protocol.methods import SessionReadRequestDto
from eidos_runtime.protocol.session_fork import SessionForkRequest, SessionForkResponse
from eidos_runtime.runtime.events import RuntimeEvents


class SessionForkApplication:
    def __init__(
        self,
        history: SessionHistoryRepository,
        sessions: SessionApplication,
        lifecycle: SessionLifecycleCoordinator,
        events: RuntimeEvents,
        manager: WorktreeManager | None = None,
        snapshots: WorktreeSnapshotService | None = None,
    ) -> None:
        self.history, self.sessions = history, sessions
        self.lifecycle, self.events = lifecycle, events
        self.manager, self.snapshots = manager, snapshots

    def fork(
        self, request: SessionForkRequest, cancel: threading.Event | None = None
    ) -> SessionForkResponse:
        try:
            with self.lifecycle.hold_operation("session/fork", request.operation_id):
                replay = self.history.database.operation_result(
                    request.operation_id,
                    "session/fork",
                    request.model_dump(mode="json", by_alias=True),
                )
                if replay is not None:
                    session_id = str(replay["sessionId"])
                else:
                    if self.manager is not None:
                        pending = self.manager.lifecycle.read(
                            WorktreeLifecycleScope.SESSION_CREATE,
                            self._environment_id(request),
                        )
                        if (
                            pending is not None
                            and pending.request_hash
                            != canonical_hash(
                                request.model_dump(mode="json", by_alias=True)
                            )
                        ):
                            raise OperationConflictError("fork operation id was reused")
                    if cancel is not None and cancel.is_set():
                        raise HistoryCaptureCancelled("fork capture cancelled")
                    self.history.anchor(
                        request.source_session_id, request.source_item_id
                    )
                    source = SessionRepository(
                        self.history.database
                    ).read_session_projection(request.source_session_id)
                    if source is None:
                        raise ResourceNotFoundError("source session not found")
                    if (
                        source.worktree is not None
                        and request.target.kind == "same_workspace"
                    ):
                        raise InvalidRunStateError(
                            "managed sessions require an independent worktree"
                        )
                    session_id = str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL, "session-fork:" + request.operation_id
                        )
                    )
                    worktree_id = None
                    if request.target.kind == "new_worktree":
                        worktree_id = self._prepare_worktree(request, session_id)
                    mutation = self.history.fork(
                        request,
                        session_id=session_id,
                        worktree_id=worktree_id,
                        cancelled=cancel.is_set
                        if cancel is not None
                        else lambda: False,
                    )
                    self.events.publish(mutation)
                self._complete_environment(request)
                session = self.sessions.read_snapshot(
                    SessionReadRequestDto(sessionId=session_id)
                ).session
                return SessionForkResponse(session=session)
        except ResourceNotFoundError as error:
            raise ApplicationError("RESOURCE_NOT_FOUND", str(error)) from error
        except HistoryCaptureCancelled as error:
            raise ApplicationError(
                "SESSION_FORK_CANCELED", "conversation fork was cancelled"
            ) from error
        except HistoryLimitExceeded as error:
            raise ApplicationError("SESSION_FORK_HISTORY_LIMIT", str(error)) from error
        except InvalidRunStateError as error:
            raise ApplicationError("INVALID_STATE", str(error)) from error
        except OperationConflictError as error:
            raise ApplicationError("OPERATION_ID_REUSED", str(error)) from error
        except OperationInProgressError as error:
            raise ApplicationError("OPERATION_IN_PROGRESS", str(error)) from error
        except WorktreeError as error:
            raise ApplicationError(
                "SESSION_FORK_WORKTREE_FAILED", str(error)
            ) from error
        except (
            StorageError,
            OSError,
            sqlite3.Error,
            ValidationError,
            ValueError,
        ) as error:
            raise ApplicationError(
                "SESSION_FORK_FAILED", "could not persist conversation fork"
            ) from error

    @staticmethod
    def _environment_id(request: SessionForkRequest) -> str:
        return str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                "session-fork-environment:" + str(request.operation_id),
            )
        )

    def _complete_environment(self, request: SessionForkRequest) -> None:
        if request.target.kind != "new_worktree" or self.manager is None:
            return
        scope, operation_id = (
            WorktreeLifecycleScope.SESSION_CREATE,
            self._environment_id(request),
        )
        operation = self.manager.lifecycle.read(scope, operation_id)
        if operation is None:
            raise StorageError("fork_environment_missing")
        if operation.state == WorktreeLifecycleState.WORKTREE_CREATED:
            operation = self.manager.lifecycle.update_state(
                scope, operation_id, WorktreeLifecycleState.SESSION_CREATED
            )
        if operation.state == WorktreeLifecycleState.SESSION_CREATED:
            self.manager.lifecycle.update_state(
                scope, operation_id, WorktreeLifecycleState.COMPLETED
            )

    def _prepare_worktree(self, request: SessionForkRequest, session_id: str) -> str:
        manager, snapshots = self.manager, self.snapshots
        source = SessionRepository(self.history.database).read_session_projection(
            request.source_session_id
        )
        if (
            manager is None
            or snapshots is None
            or source is None
            or source.project is None
            or not source.project.git_available
        ):
            raise InvalidRunStateError("independent worktree requires a Git project")
        operation_id = self._environment_id(request)
        lifecycle = manager.lifecycle
        operation = lifecycle.read(WorktreeLifecycleScope.SESSION_CREATE, operation_id)
        if operation is None:
            snapshot_id = "fork-" + operation_id
            snapshot = snapshots.snapshots.read(snapshot_id)
            if snapshot is None:
                if source.worktree is not None:
                    snapshot = snapshots.save(
                        manager.read_worktree(source.worktree.worktree_id),
                        snapshot_id,
                        replace_older=False,
                    )
                else:
                    snapshot = snapshots.save_local(
                        manager.project(source.project.id),
                        workspace_root=Path(source.session.workspace_root),
                        session_id=source.session.id,
                        snapshot_id=snapshot_id,
                    )
            snapshots.verify(snapshot)
            plan = manager.prepare_create(
                source.project.workspace_root, base_ref=snapshot.head
            )
            now = datetime.now(UTC).replace(microsecond=0)
            operation = lifecycle.prepare(
                WorktreeLifecycleOperation(
                    scope=WorktreeLifecycleScope.SESSION_CREATE,
                    operation_id=operation_id,
                    request_hash=canonical_hash(
                        request.model_dump(mode="json", by_alias=True)
                    ),
                    state=WorktreeLifecycleState.PREPARED,
                    project_id=plan.project_id,
                    repository_root=source.project.workspace_root,
                    worktree_id=plan.id,
                    worktree_root=plan.worktree_root,
                    base_ref=plan.base_ref,
                    branch=plan.branch,
                    base_commit=plan.base_commit,
                    session_id=session_id,
                    snapshot_id=snapshot.id,
                    snapshot_head=snapshot.head,
                    snapshot_fingerprint=snapshot.source_fingerprint,
                    created_at=now,
                    updated_at=now,
                )
            )
        if operation.request_hash != canonical_hash(
            request.model_dump(mode="json", by_alias=True)
        ):
            raise OperationConflictError("fork operation id was reused")
        if operation.state == WorktreeLifecycleState.CLEANUP_REQUIRED:
            raise InvalidRunStateError("worktree fork requires recovery")
        snapshot = snapshots.snapshots.read(operation.snapshot_id)
        if snapshot is None:
            raise WorktreeError("worktree_snapshot_required")
        snapshots.verify(snapshot)
        worktree = manager.create_prepared(
            manager.prepared_from_lifecycle(operation), compensate_on_failure=True
        )
        if operation.state == WorktreeLifecycleState.PREPARED:
            lifecycle.update_state(
                WorktreeLifecycleScope.SESSION_CREATE,
                operation_id,
                WorktreeLifecycleState.WORKTREE_CREATED,
            )
        manager.restore_snapshot_state(
            worktree.id,
            head=snapshot.head,
            changes=snapshots.read_changes(snapshot),
            expected_fingerprint=None,
        )
        observed = manager.source_snapshot(
            Path(worktree.worktree_root), include_local_changes=True
        )
        if observed.head != snapshot.head or observed.changes != snapshots.read_changes(
            snapshot
        ):
            raise WorktreeError("worktree_snapshot_mismatch")
        return worktree.id
