from __future__ import annotations

from collections.abc import Iterable
import hashlib
import json
import time
from typing import Literal

from pydantic import Field, model_validator

from eidos_runtime.context.budget import ContextBudget, estimate_context_budget
from eidos_runtime.context.facts import CompactSummary
from eidos_runtime.model.client import ModelProfileSnapshot
from eidos_runtime.models import EidosFrozenStrictModel, JsonSafeInt
from eidos_runtime.repo_intelligence.index import RepositoryIndexSnapshot
from eidos_runtime.repo_intelligence.inventory import RepositoryInventory
from eidos_runtime.repo_intelligence.map import RepositoryMap
from eidos_runtime.repo_intelligence.retrieval import (
    RepositoryEvidence,
    RetrievalSnapshot,
)
from eidos_runtime.runtime.resolution import RuleResolutionSnapshot


class ContextPlanError(ValueError):
    pass


class ContextMessage(EidosFrozenStrictModel):
    role: Literal["system", "user", "assistant"]
    section: str = Field(min_length=1)
    content: str


class ContextSectionBudget(EidosFrozenStrictModel):
    section: str = Field(min_length=1)
    priority: int = Field(ge=0)
    max_bytes: int = Field(ge=0)
    used_bytes: int = Field(ge=0)


class ContextPlan(EidosFrozenStrictModel):
    schema_version: int = 1
    plan_id: str
    model_profile_snapshot_id: str
    model_profile_snapshot_hash: str = Field(min_length=64, max_length=64)
    rule_resolution_snapshot_id: str
    rule_resolution_snapshot_hash: str = Field(min_length=64, max_length=64)
    inventory_snapshot_id: str
    index_snapshot_id: str
    repository_map_snapshot_id: str
    user_goal: str
    recent_conversation: tuple[str, ...]
    verified_compact_summary: CompactSummary | None
    tool_facts: tuple[str, ...]
    pending_approval_facts: tuple[str, ...]
    reconciliation_facts: tuple[str, ...]
    current_diff: tuple[str, ...]
    messages: tuple[ContextMessage, ...]
    selected_evidence: tuple[RepositoryEvidence, ...]
    token_budget: ContextBudget
    section_budgets: tuple[ContextSectionBudget, ...]
    omissions: tuple[str, ...]
    diagnostics: tuple[str, ...]
    created_at_ms: JsonSafeInt
    snapshot_hash: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def verify_hash(self) -> ContextPlan:
        payload = self.model_dump(
            mode="json", exclude={"plan_id", "snapshot_hash", "created_at_ms"}
        )
        digest = _hash(payload)
        if self.snapshot_hash != digest or self.plan_id != f"plan_{digest}":
            raise ValueError("context plan hash mismatch")
        return self

    def for_model_attempt(self, model_attempt_id: str) -> ContextSnapshot:
        if not model_attempt_id:
            raise ValueError("model_attempt_id is required")
        payload = {
            "schema_version": 1,
            "model_attempt_id": model_attempt_id,
            "plan_id": self.plan_id,
            "plan_hash": self.snapshot_hash,
            "messages": [message.model_dump(mode="json") for message in self.messages],
            "selected_evidence": [item.model_dump(mode="json") for item in self.selected_evidence],
        }
        digest = _hash(payload)
        return ContextSnapshot(
            schema_version=1,
            model_attempt_id=model_attempt_id,
            plan_id=self.plan_id,
            plan_hash=self.snapshot_hash,
            plan=self,
            snapshot_id=f"context_{digest}",
            snapshot_hash=digest,
            created_at_ms=int(time.time() * 1000),
        )


class ContextSnapshot(EidosFrozenStrictModel):
    schema_version: int = 1
    model_attempt_id: str
    plan_id: str
    plan_hash: str = Field(min_length=64, max_length=64)
    plan: ContextPlan
    snapshot_id: str
    snapshot_hash: str = Field(min_length=64, max_length=64)
    created_at_ms: JsonSafeInt

    @model_validator(mode="after")
    def verify_hash(self) -> ContextSnapshot:
        payload = {
            "schema_version": self.schema_version,
            "model_attempt_id": self.model_attempt_id,
            "plan_id": self.plan_id,
            "plan_hash": self.plan_hash,
            "messages": [message.model_dump(mode="json") for message in self.plan.messages],
            "selected_evidence": [item.model_dump(mode="json") for item in self.plan.selected_evidence],
        }
        digest = _hash(payload)
        if self.snapshot_hash != digest or self.snapshot_id != f"context_{digest}":
            raise ValueError("context snapshot hash mismatch")
        if self.plan_hash != self.plan.snapshot_hash or self.plan_id != self.plan.plan_id:
            raise ValueError("context snapshot plan mismatch")
        return self


class ContextPlanner:
    def build(
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
        self._validate_snapshots(inventory, index, repository_map, retrieval)
        model_profile_hash = _hash(model_profile.model_dump(mode="json"))
        messages: list[ContextMessage] = []
        if instruction := rule_snapshot.model_instruction():
            messages.append(ContextMessage(
                role="system", section="rules", content=instruction
            ))
        messages.append(ContextMessage(
            role="user", section="goal", content=user_goal
        ))
        messages.extend(ContextMessage(role="user", section="pending_approval", content=value) for value in pending_approval_facts)
        messages.extend(ContextMessage(role="user", section="reconciliation", content=value) for value in reconciliation_facts)
        if compact_summary is not None:
            messages.append(ContextMessage(
                role="user", section="verified_summary",
                content=compact_summary.model_dump_json(),
            ))
        messages.extend(ContextMessage(role="user", section="conversation", content=value) for value in recent_conversation)
        messages.extend(ContextMessage(role="user", section="tool_fact", content=value) for value in tool_facts)
        messages.extend(ContextMessage(role="user", section="diff", content=value) for value in current_diff)
        evidence = self._fresh_evidence(inventory, retrieval)
        remaining_bytes = max(0, (model_profile.context_window_tokens - model_profile.max_output_tokens) * 4)
        selected: list[RepositoryEvidence] = []
        used_bytes = sum(len(message.content.encode("utf-8")) for message in messages)
        for item in evidence:
            item_bytes = len(item.text.encode("utf-8"))
            if used_bytes + item_bytes > remaining_bytes:
                continue
            selected.append(item)
            messages.append(ContextMessage(role="user", section="repository_evidence", content=f"{item.path}\n{item.text}"))
            used_bytes += item_bytes
        payload = {
            "system": rule_snapshot.model_instruction() or "",
            "messages": [message.model_dump(mode="json") for message in messages],
        }
        budget = estimate_context_budget(
            payload,
            context_window_tokens=model_profile.context_window_tokens,
            request_max_output_tokens=model_profile.max_output_tokens,
            message_count=len(messages),
            tool_call_count=0,
            tool_result_count=0,
        )
        mandatory_bytes = sum(
            len(item.encode("utf-8"))
            for item in (*pending_approval_facts, *reconciliation_facts, user_goal)
        )
        if mandatory_bytes > remaining_bytes:
            raise ContextPlanError("protected context is over budget")
        sections = _section_budgets(messages, remaining_bytes)
        diagnostics: tuple[str, ...] = ()
        omissions = tuple(
            "repository_evidence_budget" for item in evidence if item not in selected
        )
        payload_for_hash = {
            "schema_version": 1,
            "model_profile_snapshot_id": f"profile_{model_profile_hash}",
            "model_profile_snapshot_hash": model_profile_hash,
            "rule_resolution_snapshot_id": rule_snapshot.id,
            "rule_resolution_snapshot_hash": rule_snapshot.snapshot_hash,
            "inventory_snapshot_id": inventory.snapshot_id,
            "index_snapshot_id": index.snapshot_id,
            "repository_map_snapshot_id": repository_map.snapshot_id,
            "user_goal": user_goal,
            "recent_conversation": recent_conversation,
            "verified_compact_summary": compact_summary.model_dump(mode="json") if compact_summary else None,
            "tool_facts": tool_facts,
            "pending_approval_facts": pending_approval_facts,
            "reconciliation_facts": reconciliation_facts,
            "current_diff": current_diff,
            "messages": [message.model_dump(mode="json") for message in messages],
            "selected_evidence": [item.model_dump(mode="json") for item in selected],
            "token_budget": budget.model_dump(mode="json"),
            "section_budgets": [item.model_dump(mode="json") for item in sections],
            "omissions": omissions,
            "diagnostics": diagnostics,
        }
        digest = _hash(payload_for_hash)
        return ContextPlan(
            schema_version=1,
            plan_id=f"plan_{digest}",
            model_profile_snapshot_id=f"profile_{model_profile_hash}",
            model_profile_snapshot_hash=model_profile_hash,
            rule_resolution_snapshot_id=rule_snapshot.id,
            rule_resolution_snapshot_hash=rule_snapshot.snapshot_hash,
            inventory_snapshot_id=inventory.snapshot_id,
            index_snapshot_id=index.snapshot_id,
            repository_map_snapshot_id=repository_map.snapshot_id,
            user_goal=user_goal,
            recent_conversation=recent_conversation,
            verified_compact_summary=compact_summary,
            tool_facts=tool_facts,
            pending_approval_facts=pending_approval_facts,
            reconciliation_facts=reconciliation_facts,
            current_diff=current_diff,
            messages=tuple(messages),
            selected_evidence=tuple(selected),
            token_budget=budget,
            section_budgets=sections,
            omissions=omissions,
            diagnostics=diagnostics,
            created_at_ms=int(time.time() * 1000),
            snapshot_hash=digest,
        )

    @staticmethod
    def _validate_snapshots(
        inventory: RepositoryInventory,
        index: RepositoryIndexSnapshot,
        repository_map: RepositoryMap,
        retrieval: RetrievalSnapshot,
    ) -> None:
        if not inventory.complete or not index.complete:
            raise ContextPlanError("complete repository snapshots are required")
        if (
            inventory.snapshot_id != index.inventory_snapshot_id
            or inventory.repository_id != index.repository_id
            or inventory.generation != index.inventory_generation
            or inventory.snapshot_id != retrieval.inventory_snapshot_id
            or index.snapshot_id != retrieval.index_snapshot_id
            or inventory.snapshot_id != repository_map.inventory_snapshot_id
        ):
            raise ContextPlanError("stale repository snapshot generation")

    @staticmethod
    def _fresh_evidence(
        inventory: RepositoryInventory,
        retrieval: RetrievalSnapshot,
    ) -> tuple[RepositoryEvidence, ...]:
        hashes = {record.path: record.content_hash for record in inventory.files}
        selected: list[RepositoryEvidence] = []
        for result in retrieval.results:
            for evidence in result.evidence:
                if evidence.path not in hashes or evidence.file_hash != hashes[evidence.path]:
                    raise ContextPlanError("stale repository evidence")
                selected.append(evidence)
        return tuple(selected)


def _section_budgets(messages: Iterable[ContextMessage], max_bytes: int) -> tuple[ContextSectionBudget, ...]:
    totals: dict[str, int] = {}
    for message in messages:
        totals[message.section] = totals.get(message.section, 0) + len(message.content.encode("utf-8"))
    priority = {
        "pending_approval": 0, "reconciliation": 1, "goal": 2,
        "rules": 0, "verified_summary": 3, "diff": 4, "conversation": 5,
        "tool_fact": 6, "repository_evidence": 7,
    }
    return tuple(
        ContextSectionBudget(
            section=section,
            priority=priority.get(section, 99),
            max_bytes=max_bytes,
            used_bytes=used,
        )
        for section, used in sorted(totals.items(), key=lambda item: (priority.get(item[0], 99), item[0]))
    )


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")).hexdigest()


__all__ = [
    "ContextMessage",
    "ContextPlan",
    "ContextPlanError",
    "ContextPlanner",
    "ContextSectionBudget",
    "ContextSnapshot",
]
