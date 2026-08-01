from __future__ import annotations

from pathlib import Path

from eidos_runtime.application.repository import (
    RepositoryApplication,
    RepositoryApplicationFactory,
)
from eidos_runtime.db.database import Database
from eidos_runtime.persistence.repository_intelligence import (
    RepositoryIntelligenceRepository,
)


def test_repository_application_persists_complete_build_and_restores_it_on_startup(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    source = root / "main.py"
    source.write_text("def main() -> str:\n    return 'ok'\n", encoding="utf-8")
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    database = Database(data)
    database.initialize()
    try:
        repository = RepositoryIntelligenceRepository(database)
        application = RepositoryApplication(root, repository=repository)

        built = application.build()

        assert built.complete is True
        assert built.persisted_snapshot is not None
        assert application.initialize_recovery().complete is True
        assert application.initialize_recovery().reconciliation_required is False
        assert application.restore_latest_complete() == built.persisted_snapshot

        source.write_text("def main() -> str:\n    return 'changed'\n", encoding="utf-8")

        assert application.initialize_recovery().reconciliation_required is True

        restarted = RepositoryApplication(root, repository=repository)
        rebuilt = restarted.build()
        assert rebuilt.inventory.generation > built.inventory.generation
        assert rebuilt.index is not None and built.index is not None
        assert rebuilt.index.index_generation > built.index.index_generation
    finally:
        database.close()


def test_repository_application_factory_is_workspace_identity_scoped(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    database = Database(tmp_path / "factory-data")
    database.initialize()
    try:
        repository = RepositoryIntelligenceRepository(database)
        factory = RepositoryApplicationFactory(lambda: repository)

        first = factory.for_workspace(first_root)
        same = factory.for_workspace(first_root)
        second = factory.for_workspace(second_root)

        assert first is same
        assert first is not second
        assert first.workspace_identity != second.workspace_identity
    finally:
        database.close()
