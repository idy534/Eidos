from __future__ import annotations

from pathlib import Path

import pytest

from eidos_runtime.repo_intelligence.index import RepositoryIndexer
from eidos_runtime.repo_intelligence.inventory import RepositoryInventoryBuilder
from eidos_runtime.repo_intelligence.retrieval import (
    RepositoryRetrievalQuery,
    RepositoryRetriever,
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
    retriever = RepositoryRetriever(inventory, index)
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
    assert any(reason.signal == "exact_symbol" for reason in first.results[0].reasons)
    assert all(
        evidence.inventory_generation == inventory.generation
        and evidence.index_generation == index.index_generation
        for result in first.results
        for evidence in result.evidence
    )
    assert sum(len(evidence.text.encode("utf-8")) for result in first.results for evidence in result.evidence) <= query.max_evidence_bytes


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

    with pytest.raises(ValueError, match="generation"):
        RepositoryRetriever(changed_inventory, index)
