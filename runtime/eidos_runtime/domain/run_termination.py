from __future__ import annotations

from typing import Literal

from pydantic import Field

from eidos_runtime.models import EidosFrozenStrictModel


class RunTermination(EidosFrozenStrictModel):
    """Facts explaining a terminal transition, without a second Run status."""

    stage: Literal["model", "tool_validation", "context", "runtime", "lifecycle"]
    reason: str = Field(min_length=1, max_length=128)
    step_id: str | None = Field(default=None, min_length=1, max_length=128)
    model_attempt_id: str | None = Field(default=None, min_length=1, max_length=128)
    tool_name: str | None = Field(default=None, min_length=1, max_length=128)
    validation_path: str | None = Field(default=None, min_length=1, max_length=256)
    http_status: int | None = Field(default=None, ge=100, le=599)
