from __future__ import annotations

from eidos_runtime.repo_intelligence.watcher import (
    RepositoryChange,
    RepositoryWatchController,
    coalesce_changes,
)
from pathlib import Path
import threading
import time


def test_watcher_events_are_only_coalesced_invalidation_signals() -> None:
    result = coalesce_changes([
        ("modified", "src/main.py"),
        ("deleted", "src/main.py"),
        ("added", "README.md"),
        ("modified", "README.md"),
    ])

    assert result == (
        RepositoryChange(path="README.md", change="modified"),
        RepositoryChange(path="src/main.py", change="deleted"),
    )


def test_absolute_watch_event_is_normalized_against_frozen_root(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    inside = root / "src" / "main.py"
    outside = tmp_path / "outside.py"

    result = coalesce_changes(
        [("modified", str(inside)), ("modified", str(outside))],
        root=root,
    )

    assert result == (RepositoryChange(path="src/main.py", change="modified"),)


def test_real_watcher_reports_relative_add_modify_rename_delete_and_stops(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    controller = RepositoryWatchController(root)
    stop = threading.Event()
    batches: list[tuple[RepositoryChange, ...]] = []
    worker = threading.Thread(target=controller.run, args=(stop, batches.append))
    worker.start()
    time.sleep(0.2)
    original = root / "first.py"
    renamed = root / "renamed.py"
    original.write_text("one\n", encoding="utf-8")
    time.sleep(0.3)
    original.write_text("two\n", encoding="utf-8")
    time.sleep(0.3)
    original.rename(renamed)
    time.sleep(0.3)
    renamed.unlink()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        paths = {change.path for batch in batches for change in batch}
        if {"first.py", "renamed.py"} <= paths:
            break
        time.sleep(0.05)
    stop.set()
    worker.join(timeout=3)

    assert worker.is_alive() is False
    changes = [change for batch in batches for change in batch]
    assert {change.path for change in changes} >= {"first.py", "renamed.py"}
    assert all(not Path(change.path).is_absolute() for change in changes)
