from __future__ import annotations

from pathlib import Path

import pytest

from eidos_runtime.repo_intelligence.index import RepositoryIndexer
from eidos_runtime.repo_intelligence.inventory import RepositoryInventoryBuilder
from eidos_runtime.repo_intelligence.map import RepositoryMapBuilder
from eidos_runtime.repo_intelligence.retrieval import (
    RepositoryRetrievalQuery,
    RepositoryRetriever,
)
from eidos_runtime.db.layout import RepositoryDatabase
from eidos_runtime.persistence.repository_intelligence import (
    RepositoryIntelligenceRepository,
    RepositoryWorkspaceIdentity,
)


def test_hybrid_retrieval_is_deterministic_explainable_and_exact_symbols_rank_first(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "auth.py").write_text(
        "def authenticate_user(token):\n    return token\n",
        encoding="utf-8",
    )
    (root / "auth_test.py").write_text(
        "def test_authenticate_user():\n    assert authenticate_user('x')\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("Authentication overview\n", encoding="utf-8")
    inventory = RepositoryInventoryBuilder(root).build()
    index = RepositoryIndexer(root).build(inventory)
    database = RepositoryDatabase(tmp_path / "data")
    database.initialize()
    repository = RepositoryIntelligenceRepository(database)
    repository_map = RepositoryMapBuilder(root).build(inventory)
    repository.commit_complete(
        inventory,
        index,
        repository_map,
        RepositoryWorkspaceIdentity.from_root(root),
    )
    retriever = RepositoryRetriever(inventory, index, repository)
    query = RepositoryRetrievalQuery(
        text="authenticate_user",
        mentioned_symbols=("authenticate_user",),
        max_results=5,
    )

    first = retriever.retrieve(query)
    second = retriever.retrieve(query)

    assert first.snapshot_hash == second.snapshot_hash
    assert first.results[0].path == "auth.py"
    assert first.results[0].score_breakdown.exact_symbol > 0
    assert first.results[0].score_breakdown.definition_match > 0
    assert any(reason.signal == "exact_symbol" for reason in first.results[0].reasons)
    assert all(
        evidence.inventory_generation == inventory.generation
        and evidence.index_generation == index.index_generation
        for result in first.results
        for evidence in result.evidence
    )
    assert sum(len(evidence.text.encode("utf-8")) for result in first.results for evidence in result.evidence) <= query.max_evidence_bytes
    assert retriever.retrieve(RepositoryRetrievalQuery(text="unrelated_zqxj")).results == ()
    assert retriever.retrieve(RepositoryRetrievalQuery(
        text="unrelated_zqxj", mentioned_symbols=("unrelated_zqxj",),
    )).results == ()
    related_tests = retriever.retrieve(RepositoryRetrievalQuery(
        text="authenticate_user",
        mentioned_paths=("auth.py",),
        mentioned_symbols=("authenticate_user",),
    ))
    promoted = next(
        result for result in related_tests.results if result.path == "auth_test.py"
    )
    assert promoted.score_breakdown.test_source > 0
    database.close()


def test_retrieval_rejects_mixed_or_incomplete_snapshots(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "main.py"
    source.write_text("def main():\n    return 1\n", encoding="utf-8")
    inventory = RepositoryInventoryBuilder(root).build()
    indexer = RepositoryIndexer(root)
    index = indexer.build(inventory)
    source.write_text("def changed():\n    return 2\n", encoding="utf-8")
    changed_inventory = RepositoryInventoryBuilder(root).build()

    database = RepositoryDatabase(tmp_path / "data")
    database.initialize()
    repository = RepositoryIntelligenceRepository(database)
    repository_map = RepositoryMapBuilder(root).build(inventory)
    repository.commit_complete(
        inventory,
        index,
        repository_map,
        RepositoryWorkspaceIdentity.from_root(root),
    )
    with pytest.raises(ValueError, match="generation"):
        RepositoryRetriever(changed_inventory, index, repository)
    database.close()


def test_retrieval_does_not_materialize_the_full_fts_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "main.py").write_text(
        "def target_symbol():\n    return 1\n", encoding="utf-8"
    )
    inventory = RepositoryInventoryBuilder(root).build()
    index = RepositoryIndexer(root).build(inventory)
    database = RepositoryDatabase(tmp_path / "data")
    database.initialize()
    repository = RepositoryIntelligenceRepository(database)
    repository.commit_complete(
        inventory,
        index,
        RepositoryMapBuilder(root).build(inventory),
        RepositoryWorkspaceIdentity.from_root(root),
    )

    def fail_full_materialization(_snapshot_id: str) -> tuple[object, ...]:
        raise AssertionError("retrieval must use bounded indexed candidates")

    monkeypatch.setattr(repository, "list_fts_documents", fail_full_materialization)
    retriever = RepositoryRetriever(inventory, index, repository)

    result = retriever.retrieve(RepositoryRetrievalQuery(
        text="target_symbol", mentioned_symbols=("target_symbol",)
    ))

    assert result.results[0].path == "main.py"
    database.close()


def _retriever(tmp_path: Path, files: dict[str, str]):
    root = tmp_path / "repo"
    for relative, text in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    inventory = RepositoryInventoryBuilder(root).build()
    index = RepositoryIndexer(root).build(inventory)
    database = RepositoryDatabase(tmp_path / "data")
    database.initialize()
    repository = RepositoryIntelligenceRepository(database)
    repository.commit_complete(
        inventory, index, RepositoryMapBuilder(root).build(inventory),
        RepositoryWorkspaceIdentity.from_root(root),
    )
    return RepositoryRetriever(inventory, index, repository), database


def test_small_talk_without_repository_vocabulary_retrieves_nothing(tmp_path: Path) -> None:
    retriever, database = _retriever(tmp_path, {
        "greet.py": "def history():\n    return 'hi'\n\ndef hint():\n    return 'hello there'\n",
    })
    try:
        for text in ("hi", "hello there", "继续", "帮我翻译一下"):
            assert retriever.retrieve(RepositoryRetrievalQuery(text=text)).results == (), text
        # A request that names repository vocabulary still searches.
        assert retriever.retrieve(RepositoryRetrievalQuery(text="where is history defined")).results
    finally:
        database.close()


def test_bm25_strength_rewards_better_matches() -> None:
    from eidos_runtime.repo_intelligence.retrieval import _bm25_strength

    # FTS5 bm25() is more negative for better matches.
    assert _bm25_strength(-6.0) > _bm25_strength(-0.2) > 0


def test_evidence_is_bounded_windowed_and_deduplicated(tmp_path: Path) -> None:
    from eidos_runtime.repo_intelligence.retrieval import MAX_EVIDENCE_ITEM_BYTES

    # Stay under the indexer's per-file chunk cap so every method has a chunk.
    filler = "".join(
        f"    def method_{index}(self):\n        \"\"\"{'Explains the behavior of this method. ' * 3}\"\"\"\n        return {index}\n\n"
        for index in range(100)
    )
    helper = "def shared_helper_function():\n    return 'shared'\n"
    retriever, database = _retriever(tmp_path, {
        "big.py": "class LargeHolder:\n" + filler + "    def needle_target(self):\n        return 'needle'\n",
        "copy_a/helper.py": helper,
        "copy_b/helper.py": helper,
    })
    try:
        needle = retriever.retrieve(RepositoryRetrievalQuery(text="needle_target", mentioned_symbols=("needle_target",)))
        texts = [evidence for result in needle.results for evidence in result.evidence]
        assert texts and all(len(evidence.text.encode("utf-8")) <= MAX_EVIDENCE_ITEM_BYTES for evidence in texts)
        # The class chunk overlaps the selected method, so it is not repeated.
        assert sum("needle_target" in evidence.text for evidence in texts) == 1
        assert texts[0].text.startswith("def needle_target")

        holder = retriever.retrieve(RepositoryRetrievalQuery(text="LargeHolder needle_target"))
        window = next(evidence for result in holder.results for evidence in result.evidence if evidence.path == "big.py")
        assert len(window.text.encode("utf-8")) <= MAX_EVIDENCE_ITEM_BYTES

        shared = retriever.retrieve(RepositoryRetrievalQuery(text="shared_helper_function"))
        bodies = [evidence.text for result in shared.results for evidence in result.evidence if "return 'shared'" in evidence.text]
        assert len(bodies) == 1
    finally:
        database.close()


def test_large_definition_window_centers_on_the_requested_name(tmp_path: Path) -> None:
    from eidos_runtime.repo_intelligence.retrieval import MAX_EVIDENCE_ITEM_BYTES

    lines = [f"    value_{index} = {index}\n" for index in range(1500)]
    lines[1200] = "    marker_symbol_xyz = 'found'\n"
    retriever, database = _retriever(tmp_path, {"long.py": "def long_function():\n" + "".join(lines)})
    try:
        result = retriever.retrieve(RepositoryRetrievalQuery(text="long_function marker_symbol_xyz"))
        evidence = next(evidence for item in result.results for evidence in item.evidence if evidence.kind != "file")
        assert "marker_symbol_xyz" in evidence.text
        assert len(evidence.text.encode("utf-8")) <= MAX_EVIDENCE_ITEM_BYTES
        assert evidence.start_line > 0
        assert evidence.end_line - evidence.start_line + 1 == len(evidence.text.splitlines())
        assert evidence.text.splitlines()[1201 - evidence.start_line].strip().startswith("marker_symbol_xyz")
    finally:
        database.close()


def test_symbol_hits_show_definitions_and_typos_match_identifiers(tmp_path: Path) -> None:
    retriever, database = _retriever(tmp_path, {
        "auth.py": "def authenticate_user(token):\n    return token\n",
    })
    try:
        exact = retriever.retrieve(RepositoryRetrievalQuery(
            text="authenticate_user", mentioned_symbols=("authenticate_user",),
        ))
        assert exact.results[0].evidence[0].text.startswith("def authenticate_user(token):")
        typo = retriever.retrieve(RepositoryRetrievalQuery(text="fix authentcate_user"))
        assert any(result.score_breakdown.fuzzy_symbol > 0 and result.path == "auth.py" for result in typo.results)
        assert RepositoryRetrievalQuery(text="x").max_evidence_bytes == 24 * 1024
    finally:
        database.close()
