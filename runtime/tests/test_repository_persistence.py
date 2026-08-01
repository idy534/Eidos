from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from eidos_runtime.db.database import Database
from eidos_runtime.persistence.repository_intelligence import (
    RepositoryFtsDocument,
    RepositoryIntelligenceRepository,
    RepositoryIntelligenceSnapshot,
    RepositoryWorkspaceIdentity,
)
from eidos_runtime.repo_intelligence.index import (
    RepositoryIndexSnapshot,
    RepositoryIndexer,
)
from eidos_runtime.repo_intelligence.inventory import (
    RepositoryInventory,
    RepositoryInventoryBuilder,
)


def _database(tmp_path: Path) -> Database:
    data_directory = tmp_path / "data"
    data_directory.mkdir(mode=0o700)
    database = Database(data_directory)
    database.initialize()
    assert database.health_state == "ready"
    return database


def _workspace_identity(root: Path) -> RepositoryWorkspaceIdentity:
    return RepositoryWorkspaceIdentity.from_root(root)


def _complete_generation(
    root: Path,
) -> tuple[RepositoryInventory, RepositoryIndexSnapshot]:
    inventory = RepositoryInventoryBuilder(root).build()
    assert inventory.complete is True
    index = RepositoryIndexer(root).build(inventory)
    assert index.complete is True
    return inventory, index


def _workspace_with_python_source(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    source_directory = root / "src"
    source_directory.mkdir(parents=True)
    (source_directory / "service.py").write_text(
        "def useful_service(value: str) -> str:\n    return value.upper()\n",
        encoding="utf-8",
    )
    return root


def test_repository_intelligence_complete_generation_roundtrips_across_database_reopen(
    tmp_path: Path,
) -> None:
    root = _workspace_with_python_source(tmp_path)
    workspace_identity = _workspace_identity(root)
    inventory, index = _complete_generation(root)
    database = _database(tmp_path)
    try:
        repository = RepositoryIntelligenceRepository(database)
        committed = repository.commit_complete(
            inventory,
            index,
            workspace_identity,
        )

        assert isinstance(committed, RepositoryIntelligenceSnapshot)
        assert committed.complete is True
        assert committed.workspace_identity == workspace_identity
        assert committed.inventory == inventory
        assert committed.index == index
        assert committed.inventory_generation == inventory.generation
        assert committed.index_generation == index.index_generation
        first_fts_documents = repository.list_fts_documents(index.snapshot_id)
        assert first_fts_documents
        assert all(
            isinstance(document, RepositoryFtsDocument)
            for document in first_fts_documents
        )
        assert {
            document.path for document in first_fts_documents
        } == {"src/service.py"}
        assert {
            document.index_snapshot_id for document in first_fts_documents
        } == {index.snapshot_id}
        matches = repository.query_fts_bm25(
            index.snapshot_id, "service", deadline_ms=500
        )
        assert matches
        assert all(
            match.document.index_snapshot_id == index.snapshot_id
            for match in matches
        )
        assert repository.exact_symbol_lookup(index.snapshot_id, "useful_service")
        assert repository.path_lookup(index.snapshot_id, "src/service.py")
        assert repository.query_fts_bm25(
            "another-index", "service", deadline_ms=500
        ) == ()
    finally:
        database.close()

    reopened = Database(tmp_path / "data")
    reopened.initialize()
    try:
        repository = RepositoryIntelligenceRepository(reopened)
        restored = repository.read_latest_complete(
            inventory.repository_id,
            workspace_identity,
        )

        assert restored is not None
        assert restored == committed
        assert restored.workspace_identity == workspace_identity
        assert restored.inventory == inventory
        assert restored.index == index
        assert repository.list_fts_documents(index.snapshot_id) == first_fts_documents
    finally:
        reopened.close()


def test_incomplete_repository_candidate_never_displaces_last_complete_generation(
    tmp_path: Path,
) -> None:
    root = _workspace_with_python_source(tmp_path)
    workspace_identity = _workspace_identity(root)
    inventory, index = _complete_generation(root)
    database = _database(tmp_path)
    try:
        repository = RepositoryIntelligenceRepository(database)
        committed = repository.commit_complete(
            inventory,
            index,
            workspace_identity,
        )
        authoritative_documents = repository.list_fts_documents(index.snapshot_id)

        (root / "src" / "replacement.py").write_text(
            "def replacement() -> None:\n    pass\n",
            encoding="utf-8",
        )
        incomplete = RepositoryInventoryBuilder(root, max_entries=1).build()
        assert incomplete.complete is False

        recorded = repository.record_incomplete(
            incomplete,
            None,
            workspace_identity,
        )

        assert recorded.complete is False
        assert recorded.index is None
        assert repository.read_latest_complete(
            inventory.repository_id,
            workspace_identity,
        ) == committed
        assert repository.list_fts_documents(index.snapshot_id) == authoritative_documents
    finally:
        database.close()


def test_failed_write_after_fts_population_rolls_back_repository_generation_and_all_derived_facts(
    tmp_path: Path,
) -> None:
    root = _workspace_with_python_source(tmp_path)
    workspace_identity = _workspace_identity(root)
    inventory, index = _complete_generation(root)
    database = _database(tmp_path)
    try:
        connection = database.connection()
        connection.execute(
            """
            CREATE TRIGGER repository_chunk_insert_failure
            BEFORE INSERT ON repository_chunks
            BEGIN
                SELECT RAISE(ABORT, 'repository_chunk_injected_failure');
            END
            """
        )
        connection.commit()
        repository = RepositoryIntelligenceRepository(database)

        with pytest.raises(
            sqlite3.IntegrityError,
            match="repository_chunk_injected_failure",
        ):
            repository.commit_complete(inventory, index, workspace_identity)

        assert repository.read_latest_complete(
            inventory.repository_id,
            workspace_identity,
        ) is None
        assert repository.list_fts_documents(index.snapshot_id) == ()
        for table in (
            "repository_snapshots",
            "repository_files",
            "repository_directories",
            "repository_index_generations",
            "repository_parsed_files",
            "repository_symbols",
            "repository_imports",
            "repository_references",
            "repository_chunks",
            "repository_diagnostics",
            "repository_fts",
        ):
            assert connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0] == 0
    finally:
        database.close()
