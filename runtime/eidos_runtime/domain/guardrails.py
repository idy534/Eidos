from __future__ import annotations

from pydantic import Field

from eidos_runtime.models import EidosFrozenStrictModel


class GuardrailConfigConflict(RuntimeError):
    pass


class BackgroundGuardrailPolicy(EidosFrozenStrictModel):
    """Settings-only cycle guardrails for Goals and Automations."""

    max_tokens_per_cycle: int | None = Field(default=None, ge=1_000, le=100_000_000)
    max_cost_credits_per_cycle: float | None = Field(
        default=None, gt=0, le=1_000_000, allow_inf_nan=False
    )
    max_idle_rounds: int | None = Field(default=3, ge=1, le=100)


class BackgroundGuardrailSettings(EidosFrozenStrictModel):
    revision: int = Field(default=0, ge=0, le=9_007_199_254_740_991)
    policy: BackgroundGuardrailPolicy = Field(default_factory=BackgroundGuardrailPolicy)
