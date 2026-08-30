from __future__ import annotations

from pathlib import Path

import pytest

from eidos_runtime.db.layout import RepositoryDatabase
from eidos_runtime.repo_intelligence.inventory import RepositoryInventoryBuilder
from eidos_runtime.repo_intelligence.index import RepositoryIndexer
from eidos_runtime.repo_intelligence.map import RepositoryMapBuilder
from eidos_runtime.repo_intelligence.retrieval import (
    RepositoryRetrievalQuery,
    RepositoryRetriever,
)
from eidos_runtime.persistence.repository_intelligence import (
    RepositoryIntelligenceRepository,
    RepositoryWorkspaceIdentity,
)


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


@pytest.mark.large_repository
def test_retrieval_uses_bounded_candidates_for_twenty_thousand_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "large-repository"
    root.mkdir()
    for index in range(20_000):
        (root / f"document-{index:05d}.txt").write_text(
            f"bounded retrieval document {index}\n", encoding="utf-8"
        )
    inventory = RepositoryInventoryBuilder(
        root, max_entries=20_100, max_scan_seconds=180.0
    ).build()
    indexed = RepositoryIndexer(root).build(inventory)
    database = RepositoryDatabase(tmp_path / "data")
    database.initialize()
    try:
        repository = RepositoryIntelligenceRepository(database)
        repository.commit_complete(
            inventory,
            indexed,
            RepositoryMapBuilder(root).build(inventory),
            RepositoryWorkspaceIdentity.from_root(root),
        )

        def fail_full_materialization(_snapshot_id: str) -> tuple[object, ...]:
            raise AssertionError("large retrieval must stay candidate-first")

        monkeypatch.setattr(repository, "list_fts_documents", fail_full_materialization)
        results = RepositoryRetriever(inventory, indexed, repository).retrieve(
            RepositoryRetrievalQuery(text="document-19999", max_results=12)
        )

        assert len(results.results) <= 12
        assert results.results[0].path == "document-19999.txt"
    finally:
        database.close()
