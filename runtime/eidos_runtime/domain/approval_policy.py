from __future__ import annotations

from typing import Literal

from pydantic import Field

from eidos_runtime.models import EidosFrozenStrictModel

ApprovalMode = Literal["manual", "auto_review", "full_access"]
FULL_ACCESS_WARNING_VERSION = "full-access-v1"


class ApprovalReview(EidosFrozenStrictModel):
    source: Literal["model", "mode"]
    decision: Literal["approve", "reject"]
    reason_code: str = Field(min_length=1, max_length=100)
    rationale: str = Field(min_length=1, max_length=1500)
    risk: Literal["low", "medium", "high", "critical", "unknown"] = "unknown"
    model_id: str | None = None
    policy_hash: str | None = None
    evidence_hash: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cache_read_tokens: int | None = Field(default=None, ge=0)
    duration_ms: int = Field(default=0, ge=0)
