from __future__ import annotations

from pathlib import Path
import threading

import pytest

from eidos_runtime.repo_intelligence.index import (
    IndexCanceled,
    RepositoryIndexer,
)
from eidos_runtime.repo_intelligence.inventory import RepositoryInventoryBuilder


def test_tree_sitter_index_is_generation_bound_and_deterministic(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "main.py").write_text(
        "from helper import helper\n\ndef main():\n    return helper()\n",
        encoding="utf-8",
    )
    (root / "helper.py").write_text(
        "def helper():\n    return 1\n",
        encoding="utf-8",
    )
    (root / "app.ts").write_text(
        "import { helper } from './helper';\nexport function app() { return helper(); }\n",
        encoding="utf-8",
    )

    inventory = RepositoryInventoryBuilder(root).build()
    first = RepositoryIndexer(root).build(inventory)
    second = RepositoryIndexer(root).build(inventory)

    assert first.complete is True
    assert first.inventory_generation == inventory.generation
    assert first.index_generation == second.index_generation == 1
    assert first.snapshot_hash == second.snapshot_hash
    assert {symbol.name for symbol in first.symbols} >= {"main", "helper", "app"}
    assert any(imported.path == "main.py" for imported in first.imports)
    assert all(record.file_content_hash for record in first.symbols)
    assert all(chunk.byte_end > chunk.byte_start for chunk in first.chunks)


def test_index_removes_stale_symbols_and_keeps_previous_complete_snapshot_on_cancel(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "module.py"
    source.write_text("def old_name():\n    return 1\n", encoding="utf-8")
    inventory_builder = RepositoryInventoryBuilder(root)
    first_inventory = inventory_builder.build()
    indexer = RepositoryIndexer(root)
    first = indexer.build(first_inventory)
    assert any(symbol.name == "old_name" for symbol in first.symbols)

    source.write_text("def new_name():\n    return 2\n", encoding="utf-8")
    second_inventory = inventory_builder.build()
    second = indexer.build(second_inventory)
    assert {symbol.name for symbol in second.symbols} == {"new_name"}
    assert second.index_generation == 2

    cancel = threading.Event()
    cancel.set()
    with pytest.raises(IndexCanceled):
        indexer.build(second_inventory, cancel=cancel)
    assert indexer.last_complete == second


def test_syntax_error_is_diagnostic_but_generation_remains_usable(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "good.py").write_text("def good():\n    return 1\n", encoding="utf-8")
    (root / "broken.py").write_text("def broken(:\n", encoding="utf-8")

    inventory = RepositoryInventoryBuilder(root).build()
    index = RepositoryIndexer(root).build(inventory)

    assert index.complete is True
    assert any(symbol.name == "good" for symbol in index.symbols)
    assert any(item.path == "broken.py" for item in index.diagnostics)
    assert all("tree-sitter=" in item.parser_version for item in index.parsed_files)


def test_incremental_generation_reparses_changed_file_and_removes_deleted_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    first_file = root / "first.py"
    deleted_file = root / "deleted.py"
    first_file.write_text("def first():\n    return 1\n", encoding="utf-8")
    deleted_file.write_text("def removed():\n    return 1\n", encoding="utf-8")
    inventories = RepositoryInventoryBuilder(root)
    indexer = RepositoryIndexer(root)
    first_inventory = inventories.build()
    first = indexer.build(first_inventory)
    parsed: list[str] = []
    original_parse = indexer._parse_file

    def recording_parse(record, *args):
        parsed.append(record.path)
        return original_parse(record, *args)

    monkeypatch.setattr(indexer, "_parse_file", recording_parse)
    first_file.write_text("def changed():\n    return 2\n", encoding="utf-8")
    deleted_file.unlink()
    second_inventory = inventories.build()
    second = indexer.build(second_inventory, previous=first)

    assert parsed == ["first.py"]
    assert {symbol.name for symbol in second.symbols} == {"changed"}
    assert all(item.path != "deleted.py" for item in second.parsed_files)
