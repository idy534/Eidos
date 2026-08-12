from __future__ import annotations

from pathlib import Path

import pytest

from eidos_runtime.repo_intelligence.inventory import RepositoryInventoryBuilder
from eidos_runtime.repo_intelligence.map import RepositoryMapBuilder


def test_repository_map_discovers_manifests_and_conservative_commands(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        "[project]\nname='demo'\n[tool.pytest.ini_options]\ntestpaths=['tests']\n",
        encoding="utf-8",
    )
    (root / "package.json").write_text(
        '{"scripts":{"build":"vite build","test":"vitest run"}}',
        encoding="utf-8",
    )
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "src" / "main.py").write_text("def main(): pass\n", encoding="utf-8")
    (root / "tests" / "test_main.py").write_text("def test_main(): pass\n", encoding="utf-8")

    inventory = RepositoryInventoryBuilder(root).build()
    repository_map = RepositoryMapBuilder(root).build(inventory)

    assert repository_map.snapshot_id.startswith("map_")
    assert "python" in repository_map.languages
    assert "src" in repository_map.source_roots
    assert "tests" in repository_map.test_roots
    assert "pyproject.toml" in repository_map.configuration_files
    assert any(command.command == "vite build" for command in repository_map.commands)
    assert all(command.source_path in repository_map.configuration_files for command in repository_map.commands)
    assert repository_map.git_branch is None
    assert repository_map.git_head is None


def test_repository_map_rejects_manifest_changed_after_inventory(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    manifest = root / "package.json"
    manifest.write_text('{"scripts":{"test":"vitest run"}}', encoding="utf-8")
    inventory = RepositoryInventoryBuilder(root).build()
    manifest.write_text('{"scripts":{"test":"jest"}}', encoding="utf-8")

    with pytest.raises(OSError, match="changed after inventory"):
        RepositoryMapBuilder(root).build(inventory)
