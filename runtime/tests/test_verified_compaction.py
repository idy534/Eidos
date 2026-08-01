from __future__ import annotations

import pytest

from eidos_runtime.context.facts import CompactSummary, ContextFacts, ContextItemFact
from eidos_runtime.context.verified_compaction import (
    CompactionVerificationError,
    ContextCompactionVerifier,
)


def _summary() -> CompactSummary:
    return CompactSummary(
        task_goal="finish the task",
        constraints=("keep SQLite authoritative",),
        completed_actions=("read the source",),
        workspace_changes=(),
        important_facts=("the source is typed",),
        unresolved_problems=(),
        next_actions=("run tests",),
        source_item_ids=("item-1",),
    )


def test_compaction_verification_keeps_source_provenance_and_critical_facts() -> None:
    facts = ContextFacts(
        run_id="run-1",
        session_id="session-1",
        items=(ContextItemFact(
            item_id="item-1", run_id="run-1", kind="assistant_message",
            status="completed", content="read the source",
        ),),
        reconciliation_required=True,
    )
    verified = ContextCompactionVerifier().verify(
        _summary(),
        facts,
        source_event_ids=("event-1",),
        source_tool_call_ids=("tool-1",),
        source_evidence_ids=("evidence-1",),
        pending_approval_facts=("approval-1 pending",),
        reconciliation_facts=("run-1 requires reconciliation",),
        input_range=(1, 4),
    )

    assert verified.verification_result == "verified"
    assert verified.source_item_ids == ("item-1",)
    assert verified.source_event_ids == ("event-1",)
    assert verified.pending_approval_facts == ("approval-1 pending",)
    assert verified.compaction_version == 1
    assert verified.summary_hash


def test_compaction_verification_rejects_unknown_source_items() -> None:
    facts = ContextFacts(run_id="run-1", session_id="session-1", items=())
    with pytest.raises(CompactionVerificationError, match="source item"):
        ContextCompactionVerifier().verify(
            _summary(), facts, input_range=(1, 2)
        )
