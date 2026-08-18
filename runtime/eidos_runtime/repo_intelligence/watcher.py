from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import os
import threading
from collections.abc import Callable, Iterable

from watchfiles import Change, watch

from eidos_runtime.workspace.discovery_scope import (
    DiscoveryScopeError,
    WorkspaceDiscoveryScope,
)


logger = logging.getLogger("eidos.runtime.repository.watcher")
_ROOT_IGNORE_FILES = frozenset({".gitignore", ".eidosignore"})


@dataclass(frozen=True)
class RepositoryChange:
    path: str
    change: str


def coalesce_changes(
    changes: Iterable[tuple[str, str] | tuple[Change, str]],
    *,
    root: Path | None = None,
) -> tuple[RepositoryChange, ...]:
    """Normalize events; callers must reopen and verify paths afterwards."""
    frozen_root = root.resolve(strict=True) if root is not None else None
    merged: dict[str, str] = {}
    for change, path in changes:
        value = change.name if isinstance(change, Change) else change
        if not path or "\x00" in path:
            continue
        candidate = Path(path)
        if candidate.is_absolute():
            if frozen_root is None:
                continue
            absolute = Path(os.path.abspath(candidate))
            try:
                path = absolute.relative_to(frozen_root).as_posix()
            except ValueError:
                continue
        elif frozen_root is not None:
            absolute = Path(os.path.abspath(frozen_root / candidate))
            try:
                path = absolute.relative_to(frozen_root).as_posix()
            except ValueError:
                continue
        else:
            path = candidate.as_posix()
        if not path or path == "." or path.startswith("../"):
            continue
        previous = merged.get(path)
        if previous == "added" and value == "deleted":
            merged.pop(path, None)
            continue
        merged[path] = value
    return tuple(
        RepositoryChange(path=path, change=merged[path])
        for path in sorted(merged, key=lambda value: value.encode("utf-8"))
    )


class RepositoryWatchController:
    """Background invalidation source; it never mutates an active snapshot."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=True)

    def run(
        self,
        stop: threading.Event,
        on_invalidate: Callable[[tuple[RepositoryChange, ...]], None],
    ) -> None:
        scope = self._load_discovery_scope()
        for changes in watch(
            self.root,
            stop_event=stop,
            debounce=200,
            step=50,
        ):
            normalized = coalesce_changes(
                ((change, str(path)) for change, path in changes),
                root=self.root,
            )
            if any(change.path in _ROOT_IGNORE_FILES for change in normalized):
                scope = self._load_discovery_scope()
            if scope is not None:
                normalized = tuple(
                    change
                    for change in normalized
                    if self._is_discoverable(change, scope)
                )
            if normalized:
                on_invalidate(normalized)

    def _load_discovery_scope(self) -> WorkspaceDiscoveryScope | None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        try:
            root_fd = os.open(self.root, flags)
        except OSError:
            return None
        try:
            return WorkspaceDiscoveryScope.load(root_fd)
        except DiscoveryScopeError as error:
            logger.warning(
                "repository_watch_scope_unavailable",
                extra={
                    "workspace_root": str(self.root),
                    "reason": error.code,
                },
            )
            return None
        finally:
            os.close(root_fd)

    def _is_discoverable(
        self,
        change: RepositoryChange,
        scope: WorkspaceDiscoveryScope,
    ) -> bool:
        if change.path in _ROOT_IGNORE_FILES:
            return True
        if scope.is_ignored(change.path, is_directory=False):
            return False
        path = self.root / change.path
        return not (
            path.is_dir() and scope.is_ignored(change.path, is_directory=True)
        )


__all__ = ["RepositoryChange", "RepositoryWatchController", "coalesce_changes"]
