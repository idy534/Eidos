from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Protocol

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.db.database import WorkspaceIdentity
from eidos_runtime.domain.session import SessionExecutionMode, SessionProjection
from eidos_runtime.git.errors import WorktreeError
from eidos_runtime.protocol.methods import (
    WorkspaceListDirectoryRequestDto,
    WorkspaceListDirectoryResponseDto,
    WorkspaceReadFilePreviewRequestDto,
    WorkspaceReadFilePreviewResponseDto,
)
from eidos_runtime.workspace.discovery_scope import DiscoveryScopeError
from eidos_runtime.workspace.reader import (
    WorkspacePathError,
    WorkspaceReader,
    capture_workspace_identity,
    is_workspace_discoverable_path,
)
from eidos_runtime.repo_intelligence.watcher import (
    RepositoryChange,
    RepositoryWatchController,
)
from eidos_runtime.sandbox.sensitive import SensitiveScanError


class WorkspaceSessionRepository(Protocol):
    def read_session_projection(self, session_id: str) -> SessionProjection | None: ...


class WorkspaceWorktreePort(Protocol):
    def execution_identity(self, worktree_id: str) -> WorkspaceIdentity: ...


@dataclass
class _WorkspaceWatch:
    root: Path
    stop: threading.Event
    thread: threading.Thread


class WorkspaceExplorerApplication:
    def __init__(
        self,
        sessions: WorkspaceSessionRepository,
        *,
        worktree_manager: WorkspaceWorktreePort,
        scan_text,
        on_changes: Callable[[str, tuple[RepositoryChange, ...]], None] | None = None,
        watch_factory: Callable[[Path], RepositoryWatchController] = RepositoryWatchController,
    ) -> None:
        self._sessions = sessions
        self._worktree_manager = worktree_manager
        self._scan_text = scan_text
        self._on_changes = on_changes
        self._watch_factory = watch_factory
        self._watches: dict[str, _WorkspaceWatch] = {}
        self._watch_lock = threading.RLock()

    def list_directory(
        self, request: WorkspaceListDirectoryRequestDto
    ) -> WorkspaceListDirectoryResponseDto:
        try:
            identity = self._execution_identity(request.session_id)
            self._ensure_watch(request.session_id, identity.path)
            with WorkspaceReader(identity) as reader:
                listing = reader.list_directory(request.path, limit=request.limit)
            return WorkspaceListDirectoryResponseDto.model_validate({
                "path": listing.path,
                "entries": [
                    {
                        "name": entry.name,
                        "relativePath": entry.relative_path,
                        "kind": entry.kind,
                        **(
                            {"sizeBytes": entry.size_bytes}
                            if entry.size_bytes is not None else {}
                        ),
                    }
                    for entry in listing.entries
                ],
                "truncated": listing.truncated,
            })
        except (WorkspacePathError, DiscoveryScopeError) as error:
            raise ApplicationError(_workspace_error_code(error)) from error

    def read_file_preview(
        self, request: WorkspaceReadFilePreviewRequestDto
    ) -> WorkspaceReadFilePreviewResponseDto:
        try:
            identity = self._execution_identity(request.session_id)
            with WorkspaceReader(identity) as reader:
                preview = reader.read_preview(request.path)
            content = (
                self._scan_text(preview.content)
                if preview.content is not None else None
            )
            return WorkspaceReadFilePreviewResponseDto.model_validate({
                "path": preview.path,
                "kind": preview.kind,
                "sizeBytes": preview.size_bytes,
                "truncated": preview.truncated,
                **({"content": content} if content is not None else {}),
                **({"language": preview.language} if preview.language is not None else {}),
                **({"reason": preview.reason} if preview.reason is not None else {}),
            })
        except SensitiveScanError as error:
            raise ApplicationError("WORKSPACE_SENSITIVE_CONTENT") from error
        except (WorkspacePathError, DiscoveryScopeError) as error:
            raise ApplicationError(_workspace_error_code(error)) from error

    def _execution_identity(self, session_id: str) -> WorkspaceIdentity:
        projection = self._sessions.read_session_projection(session_id)
        if projection is None:
            raise ApplicationError("RESOURCE_NOT_FOUND")
        if projection.project is None:
            raise ApplicationError("PROJECT_REQUIRED")
        if projection.session.execution_mode is SessionExecutionMode.WORKTREE:
            if projection.worktree is None:
                raise ApplicationError("WORKTREE_INVALID")
            try:
                return self._worktree_manager.execution_identity(
                    projection.worktree.worktree_id
                )
            except WorktreeError as error:
                raise ApplicationError("WORKTREE_INVALID", str(error)) from error
        return capture_workspace_identity(projection.session.workspace_root)

    def close(self) -> None:
        with self._watch_lock:
            watches = tuple(self._watches.values())
            self._watches.clear()
        for watch in watches:
            watch.stop.set()
        for watch in watches:
            watch.thread.join(timeout=3)
            if watch.thread.is_alive():
                raise RuntimeError("workspace watcher did not stop")

    def _ensure_watch(self, session_id: str, root: Path) -> None:
        if self._on_changes is None:
            return
        with self._watch_lock:
            current = self._watches.get(session_id)
            if current is not None and current.root == root:
                return
            if current is not None:
                current.stop.set()
                current.thread.join(timeout=3)
                if current.thread.is_alive():
                    raise RuntimeError("workspace watcher did not stop")
            stop = threading.Event()
            controller = self._watch_factory(root)
            thread = threading.Thread(
                name=f"workspace-watch-{session_id}",
                target=controller.run,
                args=(
                    stop,
                    lambda changes: self._on_changes_for_session(session_id, changes),
                ),
            )
            self._watches[session_id] = _WorkspaceWatch(root, stop, thread)
            thread.start()

    def _on_changes_for_session(
        self, session_id: str, changes: tuple[RepositoryChange, ...]
    ) -> None:
        callback = self._on_changes
        if callback is not None:
            visible = tuple(
                change for change in changes
                if is_workspace_discoverable_path(change.path)
            )
            if visible:
                callback(session_id, visible)


def _workspace_error_code(error: Exception) -> str:
    code = getattr(error, "code", str(error))
    return {
        "sensitive_path": "WORKSPACE_SENSITIVE_PATH",
        "workspace_unavailable": "WORKSPACE_UNAVAILABLE",
        "workspace_identity_changed": "WORKSPACE_IDENTITY_CHANGED",
        "workspace_read_timeout": "WORKSPACE_READ_TIMEOUT",
        "file_too_large": "WORKSPACE_FILE_TOO_LARGE",
        "invalid_directory_limit": "INVALID_PARAMS",
    }.get(str(code), "WORKSPACE_BOUNDARY_VIOLATION")


__all__ = ["WorkspaceExplorerApplication"]
