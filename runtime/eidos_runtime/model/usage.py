from __future__ import annotations

from decimal import Decimal

from pydantic import Field

from eidos_runtime.models import EidosFrozenStrictModel


class NormalizedUsage(EidosFrozenStrictModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    cache_creation_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    provider_reported: bool
    estimated: bool


class PricingReference(EidosFrozenStrictModel):
    id: str
    source: str
    effective_at: str


class NormalizedCost(EidosFrozenStrictModel):
    pricing_reference: PricingReference | None = None
    currency: str | None = None
    input_cost: Decimal | None = None
    output_cost: Decimal | None = None
    reasoning_cost: Decimal | None = None
    cache_cost: Decimal | None = None
    total_cost: Decimal | None = None
    estimated: bool = False
