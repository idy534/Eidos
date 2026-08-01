from __future__ import annotations

from eidos_runtime.context.facts import CompactSummary, ContextFacts
from eidos_runtime.context.plan import ContextPlan, ContextPlanner, ContextSnapshot
from eidos_runtime.context.verified_compaction import (
    ContextCompactionVerifier,
    VerifiedCompactSummary,
)
from eidos_runtime.model.client import ModelProfileSnapshot
from eidos_runtime.repo_intelligence.index import RepositoryIndexSnapshot
from eidos_runtime.repo_intelligence.inventory import RepositoryInventory
from eidos_runtime.repo_intelligence.map import RepositoryMap
from eidos_runtime.repo_intelligence.retrieval import RetrievalSnapshot
from eidos_runtime.runtime.resolution import RuleResolutionSnapshot
from eidos_runtime.persistence.context_snapshots import ContextSnapshotRepository


class ContextApplication:
    """Coordinates immutable context planning and compaction verification."""

    def __init__(
        self,
        *,
        planner: ContextPlanner | None = None,
        compaction_verifier: ContextCompactionVerifier | None = None,
        snapshots: ContextSnapshotRepository | None = None,
    ) -> None:
        self.planner = planner or ContextPlanner()
        self.compaction_verifier = compaction_verifier or ContextCompactionVerifier()
        self.snapshots = snapshots

    def plan(
        self,
        *,
        model_profile: ModelProfileSnapshot,
        rule_snapshot: RuleResolutionSnapshot,
        inventory: RepositoryInventory,
        index: RepositoryIndexSnapshot,
        repository_map: RepositoryMap,
        retrieval: RetrievalSnapshot,
        user_goal: str,
        recent_conversation: tuple[str, ...] = (),
        compact_summary: CompactSummary | None = None,
        tool_facts: tuple[str, ...] = (),
        pending_approval_facts: tuple[str, ...] = (),
        reconciliation_facts: tuple[str, ...] = (),
        current_diff: tuple[str, ...] = (),
    ) -> ContextPlan:
        return self.planner.build(
            model_profile=model_profile,
            rule_snapshot=rule_snapshot,
            inventory=inventory,
            index=index,
            repository_map=repository_map,
            retrieval=retrieval,
            user_goal=user_goal,
            recent_conversation=recent_conversation,
            compact_summary=compact_summary,
            tool_facts=tool_facts,
            pending_approval_facts=pending_approval_facts,
            reconciliation_facts=reconciliation_facts,
            current_diff=current_diff,
        )

    def verify_compaction(
        self,
        summary: CompactSummary,
        facts: ContextFacts,
        *,
        input_range: tuple[int, int],
        source_event_ids: tuple[str, ...] = (),
        source_tool_call_ids: tuple[str, ...] = (),
        source_evidence_ids: tuple[str, ...] = (),
        pending_approval_facts: tuple[str, ...] = (),
        reconciliation_facts: tuple[str, ...] = (),
    ) -> VerifiedCompactSummary:
        return self.compaction_verifier.verify(
            summary,
            facts,
            input_range=input_range,
            source_event_ids=source_event_ids,
            source_tool_call_ids=source_tool_call_ids,
            source_evidence_ids=source_evidence_ids,
            pending_approval_facts=pending_approval_facts,
            reconciliation_facts=reconciliation_facts,
        )

    def persist_for_model_attempt(
        self,
        *,
        run_id: str,
        model_attempt_id: str,
        retrieval: RetrievalSnapshot,
        plan: ContextPlan,
    ) -> ContextSnapshot:
        if self.snapshots is None:
            raise RuntimeError("context snapshot persistence is not configured")
        snapshot = plan.for_model_attempt(model_attempt_id)
        self.snapshots.persist(
            run_id=run_id, retrieval=retrieval, snapshot=snapshot
        )
        return self.snapshots.bind_running_attempt(run_id, snapshot)


__all__ = ["ContextApplication"]
