from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from eidos_runtime.memory.repository import MemoryRejected
from eidos_runtime.models import EidosFrozenStrictModel, JsonSafeInt


class MemoryFactAssessment(EidosFrozenStrictModel):
    """Compatibility DTO for assessments in previously persisted tool results."""
    supported: bool
    atomic: bool
    action: Literal["create", "reuse", "revise", "reject"]
    target_entry_id: str | None = Field(default=None, min_length=1, max_length=256)
    expected_revision: JsonSafeInt | None = Field(default=None, ge=1)
    reason: str = Field(min_length=1, max_length=300)
    input_tokens: JsonSafeInt | None = None
    output_tokens: JsonSafeInt | None = None
    model_id: str | None = None
    elapsed_ms: JsonSafeInt | None = None
    scope_id: str | None = None
    base_generation: JsonSafeInt | None = None

    @model_validator(mode="after")
    def validate_target(self) -> MemoryFactAssessment:
        targeted = self.action in {"reuse", "revise"}
        if targeted != (self.target_entry_id is not None and self.expected_revision is not None):
            raise ValueError("memory_assessment_target_invalid")
        if not targeted and (self.target_entry_id is not None or self.expected_revision is not None):
            raise ValueError("memory_assessment_target_invalid")
        return self


def require_supported_fact(supported: bool, atomic: bool) -> None:
    if not supported:
        raise MemoryRejected("memory_claim_unsupported")
    if not atomic:
        raise MemoryRejected("memory_claim_not_atomic")
