from __future__ import annotations

from pathlib import Path

from eidos_runtime.application.repository import RepositoryApplication
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
    finally:
        database.close()
