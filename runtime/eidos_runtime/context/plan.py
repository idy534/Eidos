from __future__ import annotations

import hashlib
import json
import time

from pydantic import Field, model_validator

from eidos_runtime.context.budget import ContextBudget
from eidos_runtime.model.client import (
    ModelContextItem,
    ModelProfileSnapshot,
    ModelToolDefinition,
)
from eidos_runtime.models import EidosFrozenStrictModel, JsonSafeInt
from eidos_runtime.repo_intelligence.retrieval import RepositoryEvidence
from eidos_runtime.runtime.resolution import RuleResolutionSnapshot


class ContextPlan(EidosFrozenStrictModel):
    """Immutable lineage and budget metadata for one canonical request view."""

    schema_version: int = 2
    plan_id: str
    model_profile_snapshot_id: str
    model_profile_snapshot_hash: str = Field(min_length=64, max_length=64)
    rule_resolution_snapshot_id: str
    rule_resolution_snapshot_hash: str = Field(min_length=64, max_length=64)
    inventory_snapshot_id: str | None
    index_snapshot_id: str | None
    repository_map_snapshot_id: str | None
    retrieval_snapshot_id: str | None
    selected_evidence_ids: tuple[str, ...]
    token_budget: ContextBudget
    canonical_request_hash: str = Field(min_length=64, max_length=64)
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

    def for_model_attempt(
        self,
        model_attempt_id: str,
        *,
        model_context: tuple[ModelContextItem, ...],
        instructions: str,
        tool_definitions: tuple[ModelToolDefinition, ...],
    ) -> ContextSnapshot:
        if not model_attempt_id:
            raise ValueError("model_attempt_id is required")
        request_payload = _request_payload(
            model_context, instructions, tool_definitions
        )
        if _hash(request_payload) != self.canonical_request_hash:
            raise ValueError("context snapshot request does not match plan")
        payload = {
            "schema_version": 2,
            "model_attempt_id": model_attempt_id,
            "plan_id": self.plan_id,
            "plan_hash": self.snapshot_hash,
            **request_payload,
            "inventory_snapshot_id": self.inventory_snapshot_id,
            "index_snapshot_id": self.index_snapshot_id,
            "repository_map_snapshot_id": self.repository_map_snapshot_id,
            "retrieval_snapshot_id": self.retrieval_snapshot_id,
        }
        digest = _hash(payload)
        return ContextSnapshot(
            schema_version=2,
            model_attempt_id=model_attempt_id,
            plan_id=self.plan_id,
            plan_hash=self.snapshot_hash,
            plan=self,
            model_context=model_context,
            instructions=instructions,
            tool_definitions=tool_definitions,
            inventory_snapshot_id=self.inventory_snapshot_id,
            index_snapshot_id=self.index_snapshot_id,
            repository_map_snapshot_id=self.repository_map_snapshot_id,
            retrieval_snapshot_id=self.retrieval_snapshot_id,
            snapshot_id=f"context_{digest}",
            snapshot_hash=digest,
            created_at_ms=int(time.time() * 1000),
        )


class ContextSnapshot(EidosFrozenStrictModel):
    """The exact structured payload used by one ModelAttempt."""

    schema_version: int = 2
    model_attempt_id: str
    plan_id: str
    plan_hash: str = Field(min_length=64, max_length=64)
    plan: ContextPlan
    model_context: tuple[ModelContextItem, ...]
    instructions: str
    tool_definitions: tuple[ModelToolDefinition, ...]
    inventory_snapshot_id: str | None
    index_snapshot_id: str | None
    repository_map_snapshot_id: str | None
    retrieval_snapshot_id: str | None
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
            **_request_payload(
                self.model_context, self.instructions, self.tool_definitions
            ),
            "inventory_snapshot_id": self.inventory_snapshot_id,
            "index_snapshot_id": self.index_snapshot_id,
            "repository_map_snapshot_id": self.repository_map_snapshot_id,
            "retrieval_snapshot_id": self.retrieval_snapshot_id,
        }
        digest = _hash(payload)
        if self.snapshot_hash != digest or self.snapshot_id != f"context_{digest}":
            raise ValueError("context snapshot hash mismatch")
        if self.plan_hash != self.plan.snapshot_hash or self.plan_id != self.plan.plan_id:
            raise ValueError("context snapshot plan mismatch")
        if _hash(_request_payload(
            self.model_context, self.instructions, self.tool_definitions
        )) != self.plan.canonical_request_hash:
            raise ValueError("context snapshot request does not match plan")
        return self


class ContextPlanner:
    """Captures metadata for a payload already projected by ContextBuilder."""

    def capture(
        self,
        *,
        model_profile: ModelProfileSnapshot,
        rule_snapshot: RuleResolutionSnapshot,
        model_context: tuple[ModelContextItem, ...],
        instructions: str,
        tool_definitions: tuple[ModelToolDefinition, ...],
        token_budget: ContextBudget,
        inventory_snapshot_id: str | None = None,
        index_snapshot_id: str | None = None,
        repository_map_snapshot_id: str | None = None,
        retrieval_snapshot_id: str | None = None,
        selected_evidence: tuple[RepositoryEvidence, ...] = (),
    ) -> ContextPlan:
        profile_hash = _hash(model_profile.model_dump(mode="json"))
        request_hash = _hash(_request_payload(
            model_context, instructions, tool_definitions
        ))
        payload = {
            "schema_version": 2,
            "model_profile_snapshot_id": f"profile_{profile_hash}",
            "model_profile_snapshot_hash": profile_hash,
            "rule_resolution_snapshot_id": rule_snapshot.id,
            "rule_resolution_snapshot_hash": rule_snapshot.snapshot_hash,
            "inventory_snapshot_id": inventory_snapshot_id,
            "index_snapshot_id": index_snapshot_id,
            "repository_map_snapshot_id": repository_map_snapshot_id,
            "retrieval_snapshot_id": retrieval_snapshot_id,
            "selected_evidence_ids": tuple(item.id for item in selected_evidence),
            "token_budget": token_budget.model_dump(mode="json"),
            "canonical_request_hash": request_hash,
            "omissions": (),
            "diagnostics": (),
        }
        digest = _hash(payload)
        return ContextPlan(
            **payload,
            plan_id=f"plan_{digest}",
            created_at_ms=int(time.time() * 1000),
            snapshot_hash=digest,
        )


def _request_payload(
    model_context: tuple[ModelContextItem, ...],
    instructions: str,
    tool_definitions: tuple[ModelToolDefinition, ...],
) -> dict[str, object]:
    return {
        "model_context": model_context,
        "instructions": instructions,
        "tool_definitions": [
            item.model_dump(mode="json") for item in tool_definitions
        ],
    }


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")).hexdigest()


__all__ = ["ContextPlan", "ContextPlanner", "ContextSnapshot"]
