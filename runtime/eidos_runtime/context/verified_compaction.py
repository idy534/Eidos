from __future__ import annotations

import hashlib
import json
import time
from typing import Literal

from pydantic import Field, model_validator

from eidos_runtime.context.facts import CompactSummary, ContextFacts
from eidos_runtime.context.summary_evidence import ground_note
from eidos_runtime.models import EidosFrozenStrictModel, JsonSafeInt


class CompactionVerificationError(ValueError):
    pass


class VerifiedCompactSummary(EidosFrozenStrictModel):
    schema_version: int = 1
    summary: CompactSummary
    source_item_ids: tuple[str, ...]
    source_event_ids: tuple[int, ...]
    source_tool_call_ids: tuple[str, ...]
    source_evidence_ids: tuple[str, ...]
    compaction_input_range: tuple[int, int]
    compaction_version: Literal[1, 2] = 1
    pending_approval_facts: tuple[str, ...]
    reconciliation_facts: tuple[str, ...]
    verification_result: Literal["verified"]
    verified_at_ms: JsonSafeInt
    summary_hash: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def verify_hash(self) -> VerifiedCompactSummary:
        if self.summary.continuity_notes and self.compaction_version != 2:
            raise ValueError("continuity evidence requires compaction version 2")
        payload = self.model_dump(
            mode="json", exclude={"summary_hash", "verified_at_ms"}
        )
        digest = _hash(payload)
        if digest != self.summary_hash:
            raise ValueError("verified compact summary hash mismatch")
        return self


class ContextCompactionVerifier:
    def verify(
        self,
        summary: CompactSummary,
        facts: ContextFacts,
        *,
        source_event_ids: tuple[int, ...] = (),
        source_tool_call_ids: tuple[str, ...] = (),
        source_evidence_ids: tuple[str, ...] = (),
        pending_approval_facts: tuple[str, ...] = (),
        reconciliation_facts: tuple[str, ...] = (),
        input_range: tuple[int, int],
    ) -> VerifiedCompactSummary:
        if len(input_range) != 2 or input_range[0] < 0 or input_range[1] < input_range[0]:
            raise CompactionVerificationError("compaction input range is invalid")
        available_item_ids = {item.item_id for item in facts.items}
        if facts.compact_summary is not None:
            available_item_ids.update(facts.compact_summary.source_item_ids)
        if not set(summary.source_item_ids) <= available_item_ids:
            raise CompactionVerificationError("unknown source item")
        if not set(source_event_ids) <= set(
            facts.available_event_ids
        ):
            raise CompactionVerificationError("unknown source event")
        if not set(source_tool_call_ids) <= set(
            facts.available_tool_call_ids
        ):
            raise CompactionVerificationError("unknown source tool call")
        if not set(source_evidence_ids) <= set(
            facts.available_evidence_ids
        ):
            raise CompactionVerificationError("unknown source evidence")
        if not set(summary.workspace_changes) <= set(
            facts.committed_workspace_changes
        ):
            raise CompactionVerificationError("workspace change is unsupported")
        if not set(facts.pending_approval_ids) <= set(summary.pending_approvals):
            raise CompactionVerificationError("pending approval was omitted")
        if facts.side_effects_may_exist and not summary.uncertain_side_effects:
            raise CompactionVerificationError("uncertain side effect was omitted")
        if facts.reconciliation_required and not reconciliation_facts:
            raise CompactionVerificationError("reconciliation fact was omitted")
        if any(not value for value in (*source_tool_call_ids, *source_evidence_ids)):
            raise CompactionVerificationError("source provenance is invalid")
        ordinals = {
            item.item_id: item.ordinal for item in facts.items
            if item.item_id in summary.source_item_ids
        }
        if ordinals and (
            min(ordinals.values()) < input_range[0]
            or max(ordinals.values()) > input_range[1]
        ):
            raise CompactionVerificationError("compaction input range skips source facts")
        previous = facts.compact_summary
        if previous is not None:
            if not set(previous.constraints) <= set(summary.constraints):
                raise CompactionVerificationError("continuity constraint omitted")
            retained = {note.model_dump_json() for note in summary.continuity_notes}
            if any(note.model_dump_json() not in retained for note in previous.continuity_notes):
                raise CompactionVerificationError("continuity note omitted")
            legacy = {value for value in previous.important_facts
                      if value.startswith("Model note (")}
            if not legacy <= set(summary.important_facts):
                raise CompactionVerificationError("continuity report omitted")
        originals = {item.item_id: item for item in facts.items}
        for note in summary.continuity_notes:
            if any(ref.item_id not in summary.source_item_ids for ref in note.evidence):
                raise CompactionVerificationError("continuity source is not covered")
            try:
                grounded = ground_note(note.kind, note.evidence, originals, note.interpretation)
            except ValueError as error:
                raise CompactionVerificationError("continuity evidence is invalid") from error
            if grounded != note:
                raise CompactionVerificationError("continuity evidence type is invalid")
        payload = {
            "schema_version": 1,
            "summary": summary.model_dump(mode="json"),
            "source_item_ids": tuple(dict.fromkeys(summary.source_item_ids)),
            "source_event_ids": tuple(dict.fromkeys(source_event_ids)),
            "source_tool_call_ids": tuple(dict.fromkeys(source_tool_call_ids)),
            "source_evidence_ids": tuple(dict.fromkeys(source_evidence_ids)),
            "compaction_input_range": input_range,
            "compaction_version": 2 if summary.continuity_notes else 1,
            "pending_approval_facts": tuple(dict.fromkeys(pending_approval_facts)),
            "reconciliation_facts": tuple(dict.fromkeys(reconciliation_facts)),
            "verification_result": "verified",
        }
        digest = _hash(payload)
        return VerifiedCompactSummary(
            schema_version=1,
            summary=summary,
            source_item_ids=payload["source_item_ids"],
            source_event_ids=payload["source_event_ids"],
            source_tool_call_ids=payload["source_tool_call_ids"],
            source_evidence_ids=payload["source_evidence_ids"],
            compaction_input_range=input_range,
            compaction_version=payload["compaction_version"],
            pending_approval_facts=payload["pending_approval_facts"],
            reconciliation_facts=payload["reconciliation_facts"],
            verification_result="verified",
            verified_at_ms=int(time.time() * 1000),
            summary_hash=digest,
        )


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")).hexdigest()


__all__ = [
    "CompactionVerificationError",
    "ContextCompactionVerifier",
    "VerifiedCompactSummary",
]
