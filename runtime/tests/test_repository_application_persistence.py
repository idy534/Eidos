from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from eidos_runtime.application.repository import (
    RepositoryApplication,
    RepositoryApplicationFactory,
)
from eidos_runtime.db.database import Database
from eidos_runtime.persistence.repository_intelligence import (
    RepositoryIntelligenceRepository,
)


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip()


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
        assert built.repository_map is not None
        assert built.persisted_snapshot is not None
        assert built.persisted_snapshot.repository_map == built.repository_map
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


def test_repository_application_restore_uses_only_persisted_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    manifest = root / "package.json"
    manifest.write_text(
        '{"scripts":{"test":"vitest run"}}\n', encoding="utf-8"
    )
    database = Database(tmp_path / "data")
    database.initialize()
    try:
        repository = RepositoryIntelligenceRepository(database)
        built = RepositoryApplication(root, repository=repository).build()
        assert built.repository_map is not None
        assert built.repository_map.build_systems == ("node",)
        assert built.repository_map.test_frameworks == ("vitest",)
        assert tuple(
            command.command for command in built.repository_map.commands
        ) == ("vitest run",)
        manifest.write_text(
            '{"scripts":{"test":"jest"}}\n', encoding="utf-8"
        )
        restarted = RepositoryApplication(root, repository=repository)
        monkeypatch.setattr(
            restarted.inventory_builder,
            "build",
            lambda **_kwargs: pytest.fail("restore must not rebuild inventory"),
        )
        monkeypatch.setattr(
            restarted.indexer,
            "build",
            lambda *_args, **_kwargs: pytest.fail("restore must not rebuild index"),
        )
        monkeypatch.setattr(
            restarted.map_builder,
            "build",
            lambda *_args, **_kwargs: pytest.fail(
                "restore must not rebuild repository map"
            ),
        )

        restored = restarted.restore_analysis_snapshot()

        assert restored is not None
        assert restored.repository_map == built.repository_map
        assert tuple(
            command.command for command in restored.repository_map.commands
        ) == ("vitest run",)
        assert restored.repository_map.build_systems == ("node",)
        assert restored.repository_map.test_frameworks == ("vitest",)
    finally:
        database.close()


def test_repository_application_restore_preserves_persisted_git_head(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    source = root / "main.py"
    source.write_text("value = 1\n", encoding="utf-8")
    _git(root, "add", "main.py")
    _git(root, "commit", "-qm", "generation n")
    database = Database(tmp_path / "data")
    database.initialize()
    try:
        repository = RepositoryIntelligenceRepository(database)
        built = RepositoryApplication(root, repository=repository).build()
        assert built.repository_map is not None
        generation_head = built.repository_map.git_head
        generation_branch = built.repository_map.git_branch

        source.write_text("value = 2\n", encoding="utf-8")
        _git(root, "add", "main.py")
        _git(root, "commit", "-qm", "offline head")
        assert _git(root, "rev-parse", "HEAD") != generation_head

        restored = RepositoryApplication(
            root, repository=repository
        ).restore_analysis_snapshot()

        assert restored is not None
        assert restored.repository_map is not None
        assert restored.repository_map.git_head == generation_head
        assert restored.repository_map.git_branch == generation_branch
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
