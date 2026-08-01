from __future__ import annotations

from eidos_runtime.repo_intelligence.watcher import (
    RepositoryChange,
    coalesce_changes,
)


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
