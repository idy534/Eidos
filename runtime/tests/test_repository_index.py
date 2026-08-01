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
