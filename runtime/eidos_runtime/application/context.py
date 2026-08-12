from __future__ import annotations

from eidos_runtime.context.facts import CompactSummary, ContextFacts
from eidos_runtime.context.plan import ContextPlan, ContextPlanner, ContextSnapshot
from eidos_runtime.context.verified_compaction import (
    ContextCompactionVerifier,
    VerifiedCompactSummary,
)
from eidos_runtime.model.client import ModelProfileSnapshot
from eidos_runtime.model.client import ModelContextItem, ModelToolDefinition
from eidos_runtime.context.budget import ContextBudget
from eidos_runtime.repo_intelligence.retrieval import RetrievalSnapshot
from eidos_runtime.runtime.resolution import RuleResolutionSnapshot
from eidos_runtime.persistence.context_snapshots import ContextSnapshotRepository
from eidos_runtime.persistence.verified_compaction import VerifiedCompactionRepository


class ContextApplication:
    """Coordinates immutable context planning and compaction verification."""

    def __init__(
        self,
        *,
        planner: ContextPlanner | None = None,
        compaction_verifier: ContextCompactionVerifier | None = None,
        snapshots: ContextSnapshotRepository | None = None,
        verified_compactions: VerifiedCompactionRepository | None = None,
    ) -> None:
        self.planner = planner or ContextPlanner()
        self.compaction_verifier = compaction_verifier or ContextCompactionVerifier()
        self.snapshots = snapshots
        self.verified_compactions = verified_compactions

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

    def verify_and_persist_compaction(
        self,
        *,
        run_id: str,
        summary: CompactSummary,
        input_range: tuple[int, int],
        source_event_ids: tuple[int, ...] = (),
        source_tool_call_ids: tuple[str, ...] = (),
        source_evidence_ids: tuple[str, ...] = (),
        pending_approval_facts: tuple[str, ...] = (),
        reconciliation_facts: tuple[str, ...] = (),
    ) -> VerifiedCompactSummary:
        if self.verified_compactions is None:
            raise RuntimeError("verified compaction persistence is not configured")
        return self.verified_compactions.verify_and_persist(
            run_id=run_id,
            summary=summary,
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
        retrieval: RetrievalSnapshot | None,
        plan: ContextPlan,
        model_context: tuple[ModelContextItem, ...] | None = None,
        instructions: str | None = None,
        tool_definitions: tuple[ModelToolDefinition, ...] = (),
    ) -> ContextSnapshot:
        if self.snapshots is None:
            raise RuntimeError("context snapshot persistence is not configured")
        snapshot = plan.for_model_attempt(
            model_attempt_id,
            model_context=model_context,
            instructions=instructions,
            tool_definitions=tool_definitions,
        )
        self.snapshots.persist(
            run_id=run_id, retrieval=retrieval, snapshot=snapshot
        )
        return self.snapshots.bind_running_attempt(run_id, snapshot)

    def capture_and_persist_model_attempt(
        self,
        *,
        run_id: str,
        model_attempt_id: str,
        model_profile: ModelProfileSnapshot,
        rule_snapshot: RuleResolutionSnapshot,
        model_context: tuple[ModelContextItem, ...],
        instructions: str,
        tool_definitions: tuple[ModelToolDefinition, ...],
        token_budget: ContextBudget,
        inventory_snapshot_id: str | None = None,
        index_snapshot_id: str | None = None,
        repository_map_snapshot_id: str | None = None,
        retrieval: RetrievalSnapshot | None = None,
    ) -> ContextSnapshot:
        plan = self.planner.capture(
            model_profile=model_profile,
            rule_snapshot=rule_snapshot,
            model_context=model_context,
            instructions=instructions,
            tool_definitions=tool_definitions,
            token_budget=token_budget,
            inventory_snapshot_id=inventory_snapshot_id,
            index_snapshot_id=index_snapshot_id,
            repository_map_snapshot_id=repository_map_snapshot_id,
            retrieval_snapshot_id=(retrieval.snapshot_id if retrieval else None),
            selected_evidence=tuple(
                evidence
                for result in retrieval.results
                for evidence in result.evidence
            ) if retrieval else (),
        )
        return self.persist_for_model_attempt(
            run_id=run_id,
            model_attempt_id=model_attempt_id,
            retrieval=retrieval,
            plan=plan,
            model_context=model_context,
            instructions=instructions,
            tool_definitions=tool_definitions,
        )


__all__ = ["ContextApplication"]
