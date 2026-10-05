"""Admission limits for the existing synchronous Run workers."""

from __future__ import annotations

import os

from pydantic import Field, model_validator

from eidos_runtime.models import EidosFrozenStrictModel


class RunSchedulingLimits(EidosFrozenStrictModel):
    max_active_runs: int = Field(default=4, ge=1, le=64)
    max_workers: int = Field(default=8, ge=1, le=128)

    @model_validator(mode="after")
    def validate_capacity(self) -> "RunSchedulingLimits":
        if self.max_workers < self.max_active_runs:
            raise ValueError("max_workers must be at least max_active_runs")
        return self

    @classmethod
    def from_environment(cls) -> "RunSchedulingLimits":
        """Reject invalid settings rather than silently disabling the bounds."""
        return cls(
            max_active_runs=int(os.environ.get("EIDOS_MAX_ACTIVE_RUNS", "4")),
            max_workers=int(os.environ.get("EIDOS_MAX_RUN_WORKERS", "8")),
        )
