from __future__ import annotations

from pathlib import Path

from eidos_runtime.application.repository import RepositoryApplication
from eidos_runtime.context.budget import estimate_context_budget
from eidos_runtime.context.plan import ContextPlanner
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import ModelProfileSnapshot
from eidos_runtime.repo_intelligence.retrieval import RepositoryRetrievalQuery
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


def test_two_runs_share_retrieval_artifact_and_keep_evidence_lineage(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "main.py").write_text(
        "def shared_symbol():\n    return 1\n", encoding="utf-8"
    )
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        application = RepositoryApplication(
            workspace,
            repository=store.repository_intelligence_repository(),
        )
        analysis = application.build()
        query = RepositoryRetrievalQuery(
            text="shared_symbol", mentioned_symbols=("shared_symbol",)
        )
        retrieval_a = application.retrieve(analysis, query)
        retrieval_b = application.retrieve(analysis, query)
        assert retrieval_a.snapshot_id == retrieval_b.snapshot_id
        evidence_ids = {
            evidence.id
            for result in retrieval_a.results
            for evidence in result.evidence
        }
        assert evidence_ids

        session = store.create_session(str(workspace))
        run_a, _item = store.create_run(session["id"], "inspect shared_symbol")
        rules = RuleResolutionSnapshot.create(
            workspace_root=str(workspace), cwd=str(workspace),
            budget_bytes=1024, used_bytes=0, rules=(), shadowed=(), warnings=(),
        )
        context = ({"type": "user", "content": "inspect shared_symbol"},)
        budget = estimate_context_budget(
            {"instructions": "", "messages": context, "tools": []},
            context_window_tokens=4096,
            request_max_output_tokens=512,
            message_count=1,
            tool_call_count=0,
            tool_result_count=0,
        )

        def persist(run_id: str, attempt_id: str, retrieval) -> None:
            plan = ContextPlanner().capture(
                model_profile=_profile(),
                rule_snapshot=rules,
                model_context=context,
                instructions="",
                tool_definitions=(),
                token_budget=budget,
                inventory_snapshot_id=retrieval.inventory_snapshot_id,
                index_snapshot_id=retrieval.index_snapshot_id,
                repository_map_snapshot_id=analysis.repository_map.snapshot_id,
                retrieval_snapshot_id=retrieval.snapshot_id,
                selected_evidence=tuple(
                    evidence
                    for result in retrieval.results
                    for evidence in result.evidence
                ),
            )
            snapshot = plan.for_model_attempt(
                attempt_id,
                model_context=context,
                instructions="",
                tool_definitions=(),
            )
            store.context_snapshot_repository().persist(
                run_id=run_id, retrieval=retrieval, snapshot=snapshot
            )

        persist(run_a["id"], "attempt-a", retrieval_a)
        store.fail_run(run_a["id"], "fixture")
        run_b, _item = store.create_run(session["id"], "inspect shared_symbol")
        persist(run_b["id"], "attempt-b", retrieval_b)

        repository = store.verified_compaction_repository()
        assert evidence_ids <= set(repository.load_facts(run_a["id"]).available_evidence_ids)
        assert evidence_ids <= set(repository.load_facts(run_b["id"]).available_evidence_ids)
    finally:
        store.close()
