from __future__ import annotations

from typing import Literal

from eidos_runtime.domain.input_reference import InputReference
from eidos_runtime.model.response_phase import AssistantMessagePhase
from eidos_runtime.model.replay import ModelResponseReplay

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eidos_runtime.models import EidosFrozenStrictModel


CompactNoteKind = Literal["fact", "decision", "proposal", "next_action", "unverified", "verification"]
CompactSourceKind = Literal["user", "assistant", "artifact", "execution", "mixed"]


class CompactNoteEvidence(EidosFrozenStrictModel):
    item_id: str = Field(min_length=1, max_length=256)
    quote: str = Field(min_length=1, max_length=800)


class CompactNoteSource(CompactNoteEvidence):
    tool_name: str | None = Field(default=None, max_length=256, exclude_if=lambda value: value is None)
    call_id: str | None = Field(default=None, max_length=256, exclude_if=lambda value: value is None)


class CompactContinuityNote(EidosFrozenStrictModel):
    """Source-backed historical evidence, separate from expendable narrative."""

    kind: CompactNoteKind
    source_kind: CompactSourceKind
    evidence: tuple[CompactNoteSource, ...] = Field(min_length=1, max_length=4)
    interpretation: str | None = Field(default=None, max_length=600, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def validate_claim_type(self) -> CompactContinuityNote:
        if self.kind in {"fact", "decision", "verification"} and self.interpretation is not None:
            raise ValueError("confirmed notes must use source quotes")
        if self.kind == "verification" and self.source_kind != "execution":
            raise ValueError("verification requires execution evidence")
        if self.kind == "decision" and self.source_kind != "user":
            raise ValueError("decisions require user evidence")
        if self.kind == "fact" and self.source_kind == "assistant":
            raise ValueError("assistant reports are not confirmed facts")
        return self


class _Fact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class ContextItemFact(_Fact):
    item_id: str
    run_id: str
    kind: str
    status: str
    input_references: tuple[InputReference, ...] = ()
    content: str | None = None
    phase: AssistantMessagePhase | None = None
    model_step_index: int | None = None
    model_response_id: str | None = None
    response_replay: ModelResponseReplay | None = None
    provider_call_id: str | None = None
    tool_name: str | None = None
    payload_kind: Literal["function", "custom"] | None = None
    arguments_json: str | None = None
    result_json: str | None = None
    model_result_json: str | None = None
    ordinal: int = 0


class CompactSummary(_Fact):
    task_goal: str
    constraints: tuple[str, ...]
    completed_actions: tuple[str, ...]
    workspace_changes: tuple[str, ...]
    important_facts: tuple[str, ...]
    unresolved_problems: tuple[str, ...]
    next_actions: tuple[str, ...]
    source_item_ids: tuple[str, ...]
    important_decisions: tuple[str, ...] = ()
    failed_attempts: tuple[str, ...] = ()
    pending_approvals: tuple[str, ...] = ()
    uncertain_side_effects: tuple[str, ...] = ()
    # Keep the serialized shape and hashes of old verified summaries unchanged.
    continuity_notes: tuple[CompactContinuityNote, ...] = Field(
        default=(), max_length=128, exclude_if=lambda value: not value,
    )


class ReconciliationOrigin(_Fact):
    intent_id: str
    call_id: str
    tool_name: str
    reason: str
    output_capture_error: str | None = None


class ContextFacts(_Fact):
    run_id: str
    session_id: str
    items: tuple[ContextItemFact, ...]
    compact_summary: CompactSummary | None = None
    compaction_count: int = 0
    workspace_version: int = 0
    reconciliation_epoch: int = 0
    last_diff_hash: str | None = None
    candidate_overflow: bool = False
    projection_candidate_count: int = 0
    projection_candidate_bytes: int = 0
    projection_omitted_count: int = 0
    projection_omitted_bytes: int = 0
    current_user_goal_id: str | None = None
    reconciliation_required: bool = False
    reconciliation_origins: tuple[ReconciliationOrigin, ...] = ()
    active_error_fingerprints: tuple[str, ...] = ()
    pending_approval_ids: tuple[str, ...] = ()
    side_effects_may_exist: bool = False
    committed_workspace_changes: tuple[str, ...] = ()
    available_event_ids: tuple[int, ...] = ()
    available_tool_call_ids: tuple[str, ...] = ()
    available_evidence_ids: tuple[str, ...] = ()
