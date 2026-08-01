from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
from collections.abc import Callable, Iterable

from watchfiles import Change, watch


@dataclass(frozen=True)
class RepositoryChange:
    path: str
    change: str


def coalesce_changes(
    changes: Iterable[tuple[str, str] | tuple[Change, str]],
) -> tuple[RepositoryChange, ...]:
    """Normalize events; callers must reopen and verify paths afterwards."""
    merged: dict[str, str] = {}
    for change, path in changes:
        value = change.value if isinstance(change, Change) else change
        if not path or Path(path).is_absolute() or "\x00" in path:
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
        for changes in watch(
            self.root,
            stop_event=stop,
            debounce=200,
            step=50,
        ):
            normalized = coalesce_changes((change, str(path)) for change, path in changes)
            if normalized:
                on_invalidate(normalized)


__all__ = ["RepositoryChange", "RepositoryWatchController", "coalesce_changes"]
