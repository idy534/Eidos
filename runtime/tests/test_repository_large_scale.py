from __future__ import annotations

from pathlib import Path

import pytest

from eidos_runtime.repo_intelligence.inventory import RepositoryInventoryBuilder


@pytest.mark.large_repository
def test_inventory_accepts_one_hundred_thousand_entry_fixture(tmp_path: Path) -> None:
    root = tmp_path / "large-repository"
    root.mkdir()
    for directory_index in range(100):
        directory = root / f"bucket-{directory_index:03d}"
        directory.mkdir()
        for file_index in range(1_000):
            (directory / f"file-{file_index:04d}.txt").touch()

    inventory = RepositoryInventoryBuilder(
        root,
        max_entries=110_000,
        max_scan_seconds=180.0,
    ).build()

    assert inventory.complete is True
    assert len(inventory.files) == 100_000
    assert len(inventory.directories) == 101
