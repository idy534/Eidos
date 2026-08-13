from __future__ import annotations

from pathlib import Path
import threading

import pytest

from eidos_runtime.repo_intelligence.index import (
    IndexCanceled,
    RepositoryIndexer,
)
from eidos_runtime.repo_intelligence import index as index_module
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
    unchanged_file = root / "unchanged.py"
    first_file.write_text("def first():\n    return 1\n", encoding="utf-8")
    deleted_file.write_text("def removed():\n    return 1\n", encoding="utf-8")
    unchanged_file.write_text("def unchanged():\n    return 1\n", encoding="utf-8")
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
    assert {symbol.name for symbol in second.symbols} == {"changed", "unchanged"}
    assert all(item.path != "deleted.py" for item in second.parsed_files)


def test_tree_sitter_queries_extract_semantic_references_and_all_supported_languages(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "main.py").write_text(
        "import helper\n"
        "class Runner:\n"
        "    def execute(self):\n"
        "        return helper.call(1)\n"
        "def run(value):\n"
        "    unused = value\n"
        "    return helper.call(value)\n",
        encoding="utf-8",
    )
    (root / "main.go").write_text(
        "package main\n"
        "import \"fmt\"\n"
        "type Runner struct{}\n"
        "func (r Runner) Run() { fmt.Println(r) }\n"
        "func Start() {}\n",
        encoding="utf-8",
    )
    (root / "main.js").write_text(
        "import helper from './helper';\n"
        "class Runner { run() { return helper(); } }\n"
        "function start() { return helper(); }\n"
        "const startArrow = () => helper();\n",
        encoding="utf-8",
    )
    (root / "main.ts").write_text(
        "import helper from './helper';\n"
        "interface Runner { run(): string }\n"
        "class Service { run(): string { return helper(); } }\n"
        "function start(): Runner { return new Service(); }\n",
        encoding="utf-8",
    )
    (root / "main.tsx").write_text(
        "import React from 'react';\n"
        "export function App() { return <div />; }\n",
        encoding="utf-8",
    )

    inventory = RepositoryInventoryBuilder(root).build()
    index = RepositoryIndexer(root).build(inventory)

    symbols = {(item.path, item.name, item.kind.value) for item in index.symbols}
    assert ("main.py", "run", "function") in symbols
    assert ("main.py", "Runner", "class") in symbols
    assert ("main.py", "execute", "method") in symbols
    assert ("main.go", "Runner", "type") in symbols
    assert ("main.go", "Run", "method") in symbols
    assert ("main.go", "Start", "function") in symbols
    assert ("main.js", "Runner", "class") in symbols
    assert ("main.ts", "Runner", "type") in symbols
    assert ("main.tsx", "App", "function") in symbols
    assert ("main.js", "startArrow", "function") in symbols
    assert {item.path for item in index.imports} == {
        "main.go", "main.js", "main.py", "main.ts", "main.tsx",
    }

    python_references = {
        item.name for item in index.references if item.path == "main.py"
    }
    assert "call" in python_references
    assert "unused" not in python_references


def test_tree_sitter_parser_and_query_are_cached_per_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "first.py").write_text("def first():\n    return 1\n", encoding="utf-8")
    (root / "second.py").write_text("def second():\n    return 2\n", encoding="utf-8")
    index_module._LANGUAGE_CACHE.clear()
    index_module._PARSER_CACHE.clear()
    index_module._QUERY_CACHE.clear()
    original_parser = index_module.Parser
    original_query = index_module.Query
    parser_calls = 0
    query_calls = 0

    def counting_parser(language):
        nonlocal parser_calls
        parser_calls += 1
        return original_parser(language)

    def counting_query(language, source):
        nonlocal query_calls
        query_calls += 1
        return original_query(language, source)

    monkeypatch.setattr(index_module, "Parser", counting_parser)
    monkeypatch.setattr(index_module, "Query", counting_query)
    inventory = RepositoryInventoryBuilder(root).build()
    index = RepositoryIndexer(root).build(inventory)

    assert index.complete is True
    assert parser_calls == query_calls == 1
