from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest
from pydantic import ValidationError

from eidos_runtime.application.repository import RepositoryApplication
from eidos_runtime.db.layout import RepositoryDatabase
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.db.schema import (
    LEGACY_SCHEMA_VERSION,
    SCHEMA_VERSION,
    V2_SCHEMA_SQL,
)
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
from eidos_runtime.repo_intelligence.map import RepositoryMap, RepositoryMapBuilder


def _database(tmp_path: Path) -> RepositoryDatabase:
    data_directory = tmp_path / "data"
    data_directory.mkdir(mode=0o700)
    database = RepositoryDatabase(data_directory)
    database.initialize()
    return database


def _workspace_identity(root: Path) -> RepositoryWorkspaceIdentity:
    return RepositoryWorkspaceIdentity.from_root(root)


def _complete_generation(
    root: Path,
) -> tuple[RepositoryInventory, RepositoryIndexSnapshot, RepositoryMap]:
    inventory = RepositoryInventoryBuilder(root).build()
    assert inventory.complete is True
    index = RepositoryIndexer(root).build(inventory)
    assert index.complete is True
    repository_map = RepositoryMapBuilder(root).build(inventory)
    return inventory, index, repository_map


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
    inventory, index, repository_map = _complete_generation(root)
    database = _database(tmp_path)
    try:
        repository = RepositoryIntelligenceRepository(database)
        committed = repository.commit_complete(
            inventory,
            index,
            repository_map,
            workspace_identity,
        )

        assert isinstance(committed, RepositoryIntelligenceSnapshot)
        assert committed.complete is True
        assert committed.workspace_identity == workspace_identity
        assert committed.inventory == inventory
        assert committed.index == index
        assert committed.repository_map == repository_map
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

    reopened = RepositoryDatabase(tmp_path / "data")
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
        assert restored.repository_map == repository_map
        assert repository.list_fts_documents(index.snapshot_id) == first_fts_documents
    finally:
        reopened.close()


def test_incomplete_repository_candidate_never_displaces_last_complete_generation(
    tmp_path: Path,
) -> None:
    root = _workspace_with_python_source(tmp_path)
    workspace_identity = _workspace_identity(root)
    inventory, index, repository_map = _complete_generation(root)
    database = _database(tmp_path)
    try:
        repository = RepositoryIntelligenceRepository(database)
        committed = repository.commit_complete(
            inventory,
            index,
            repository_map,
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


def test_complete_repository_snapshot_requires_generation_bound_map(
    tmp_path: Path,
) -> None:
    root = _workspace_with_python_source(tmp_path)
    identity = _workspace_identity(root)
    inventory, index, repository_map = _complete_generation(root)
    database = _database(tmp_path)
    try:
        committed = RepositoryIntelligenceRepository(database).commit_complete(
            inventory, index, repository_map, identity
        )
        without_map = committed.model_dump()
        without_map["repository_map"] = None

        with pytest.raises(
            ValidationError,
            match="complete repository generation requires index and map",
        ):
            RepositoryIntelligenceSnapshot.model_validate(without_map)

        mismatched = repository_map.model_copy(
            update={"inventory_snapshot_id": "inventory_other"}
        )
        with pytest.raises(ValueError, match="generations do not match"):
            RepositoryIntelligenceRepository(database).commit_complete(
                inventory, index, mismatched, identity
            )
    finally:
        database.close()


def test_failed_write_after_fts_population_rolls_back_repository_generation_and_all_derived_facts(
    tmp_path: Path,
) -> None:
    root = _workspace_with_python_source(tmp_path)
    workspace_identity = _workspace_identity(root)
    inventory, index, repository_map = _complete_generation(root)
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
            repository.commit_complete(
                inventory, index, repository_map, workspace_identity
            )

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


def test_repository_map_persistence_failure_keeps_previous_complete_generation(
    tmp_path: Path,
) -> None:
    root = _workspace_with_python_source(tmp_path)
    identity = _workspace_identity(root)
    inventory, index, repository_map = _complete_generation(root)
    database = _database(tmp_path)
    try:
        repository = RepositoryIntelligenceRepository(database)
        previous = repository.commit_complete(
            inventory, index, repository_map, identity
        )
        (root / "src" / "service.py").write_text(
            "def replacement() -> str:\n    return 'new'\n",
            encoding="utf-8",
        )
        next_inventory, next_index, next_map = _complete_generation(root)
        connection = database.connection()
        connection.execute(
            """
            CREATE TRIGGER repository_map_insert_failure
            BEFORE INSERT ON repository_snapshots
            WHEN NEW.repository_map_json IS NOT NULL
            BEGIN
                SELECT RAISE(ABORT, 'repository_map_injected_failure');
            END
            """
        )
        connection.commit()

        with pytest.raises(
            sqlite3.IntegrityError,
            match="repository_map_injected_failure",
        ):
            repository.commit_complete(
                next_inventory, next_index, next_map, identity
            )

        assert repository.read_latest_complete(
            inventory.repository_id, identity
        ) == previous
        assert connection.execute(
            "SELECT COUNT(*) FROM repository_snapshots"
        ).fetchone()[0] == 1
    finally:
        database.close()


def test_v1_generation_without_map_migrates_as_non_restorable(
    tmp_path: Path,
) -> None:
    root = _workspace_with_python_source(tmp_path)
    identity = _workspace_identity(root)
    inventory, index, _repository_map = _complete_generation(root)
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    database_path = data / "eidos.db"
    v1_schema = V2_SCHEMA_SQL.replace(
        "    repository_map_json TEXT,\n", ""
    ).replace(
        "\n         AND repository_map_json IS NOT NULL", ""
    )
    raw = sqlite3.connect(database_path)
    raw.executescript(v1_schema)
    raw.execute(
        """
        INSERT INTO repository_snapshots (
            id, repository_id, workspace_root, workspace_dev, workspace_inode,
            workspace_uid, inventory_generation, index_generation,
            inventory_snapshot_id, inventory_snapshot_hash, index_snapshot_id,
            index_snapshot_hash, grammar_versions_json, status, complete,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', 'complete', 1, ?)
        """,
        (
            "legacy-complete-without-map",
            inventory.repository_id,
            identity.root,
            identity.device,
            identity.inode,
            identity.owner,
            inventory.generation,
            index.index_generation,
            inventory.snapshot_id,
            inventory.snapshot_hash,
            index.snapshot_id,
            index.snapshot_hash,
            inventory.created_at_ms,
        ),
    )
    raw.execute(f"PRAGMA user_version = {LEGACY_SCHEMA_VERSION}")
    raw.commit()
    raw.close()
    database_path.chmod(0o600)

    migrated = SessionStore(data)
    migrated.initialize()
    try:
        assert migrated.health_state == "ready"
        connection = migrated.database.connection()
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        repository_connection = migrated.repository_database.connection()
        row = repository_connection.execute(
            "SELECT complete, repository_map_json FROM repository_snapshots "
            "WHERE id = ?",
            ("legacy-complete-without-map",),
        ).fetchone()
        assert tuple(row) == (0, None)

        repository = migrated.repository_intelligence_repository()
        watermark = repository.read_generation_watermark(identity)
        assert watermark.max_inventory_generation == inventory.generation
        assert watermark.max_index_generation == index.index_generation
        assert repository.read_latest_complete(
            inventory.repository_id, identity
        ) is None
        status = repository.read_status(identity)
        assert status.complete is False
        assert status.reconciliation_required is True

        rebuilt = RepositoryApplication(root, repository=repository).build()
        assert rebuilt.complete is True
        assert rebuilt.inventory.generation == inventory.generation + 1
        assert rebuilt.index is not None
        assert rebuilt.index.index_generation == index.index_generation + 1
        assert rebuilt.repository_map is not None
    finally:
        migrated.close()
