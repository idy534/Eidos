from __future__ import annotations

from pathlib import Path
import pytest
from pydantic import ValidationError

from eidos_runtime.context.facts import CompactSummary
from eidos_runtime.context.plan import ContextPlanError, ContextPlanner
from eidos_runtime.model.client import ModelProfileSnapshot
from eidos_runtime.repo_intelligence.index import RepositoryIndexer
from eidos_runtime.repo_intelligence.inventory import RepositoryInventoryBuilder
from eidos_runtime.repo_intelligence.map import RepositoryMapBuilder
from eidos_runtime.repo_intelligence.retrieval import (
    RepositoryRetrievalQuery,
    RepositoryRetriever,
)
from eidos_runtime.runtime.resolution import RuleResolutionSnapshot


def _profile() -> ModelProfileSnapshot:
    return ModelProfileSnapshot(
        provider_id="provider",
        model_id="model",
        context_window_tokens=4096,
        max_output_tokens=512,
        request_timeout_seconds=30.0,
        supports_tools=True,
        supports_json_schema_output=True,
        supports_reasoning=False,
    )


def test_context_plan_freezes_all_snapshots_and_reserves_output_budget(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "main.py").write_text("def main():\n    return 'goal'\n", encoding="utf-8")
    inventory = RepositoryInventoryBuilder(root).build()
    index = RepositoryIndexer(root).build(inventory)
    repository_map = RepositoryMapBuilder(root).build(inventory)
    retrieval = RepositoryRetriever(inventory, index).retrieve(
        RepositoryRetrievalQuery(text="main", mentioned_symbols=("main",))
    )
    rules = RuleResolutionSnapshot.create(
        workspace_root=str(root),
        cwd=str(root),
        budget_bytes=32 * 1024,
        used_bytes=0,
        rules=(),
        shadowed=(),
        warnings=(),
    )
    summary = CompactSummary(
        task_goal="goal",
        constraints=("do not delete files",),
        completed_actions=(),
        workspace_changes=(),
        important_facts=("main exists",),
        unresolved_problems=(),
        next_actions=("inspect main",),
        source_item_ids=("item-1",),
    )

    plan = ContextPlanner().build(
        model_profile=_profile(),
        rule_snapshot=rules,
        inventory=inventory,
        index=index,
        repository_map=repository_map,
        retrieval=retrieval,
        user_goal="Find main and explain it",
        recent_conversation=("The user asked for a concise explanation.",),
        compact_summary=summary,
        tool_facts=("read_file succeeded",),
        pending_approval_facts=("write_file approval is pending",),
        reconciliation_facts=("none",),
        current_diff=("main.py is unmodified",),
    )
    snapshot = plan.for_model_attempt("attempt-1")

    assert plan.plan_id.startswith("plan_")
    assert snapshot.snapshot_id.startswith("context_")
    assert plan.token_budget.usable_input_budget < _profile().context_window_tokens
    assert plan.model_profile_snapshot_hash
    assert plan.selected_evidence[0].inventory_generation == inventory.generation
    assert any("pending" in message.content for message in plan.messages)
    assert snapshot.plan == plan
    with pytest.raises(ValidationError):
        plan.user_goal = "mutated"  # type: ignore[misc]


def test_context_plan_rejects_stale_evidence_before_model_attempt(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "main.py"
    source.write_text("def main(): pass\n", encoding="utf-8")
    inventory = RepositoryInventoryBuilder(root).build()
    index = RepositoryIndexer(root).build(inventory)
    repository_map = RepositoryMapBuilder(root).build(inventory)
    retrieval = RepositoryRetriever(inventory, index).retrieve(
        RepositoryRetrievalQuery(text="main", mentioned_symbols=("main",))
    )
    source.write_text("def changed(): pass\n", encoding="utf-8")
    changed_inventory = RepositoryInventoryBuilder(root).build()
    rules = RuleResolutionSnapshot.create(
        workspace_root=str(root), cwd=str(root), budget_bytes=1024, used_bytes=0,
        rules=(), shadowed=(), warnings=(),
    )

    with pytest.raises(ContextPlanError, match="stale"):
        ContextPlanner().build(
            model_profile=_profile(), rule_snapshot=rules,
            inventory=changed_inventory, index=index, repository_map=repository_map,
            retrieval=retrieval, user_goal="goal",
        )
