from __future__ import annotations

from pydantic import Field

from eidos_runtime.models import EidosFrozenStrictModel
from eidos_runtime.model.gateway_types import WireAPI


class EidosModelError(EidosFrozenStrictModel):
    code: str
    message: str
    retryable: bool
    provider: str
    wire_api: WireAPI
    model_id: str
    attempt_id: str
    provider_request_id: str | None = None
    http_status: int | None = None
    retry_after: float | None = Field(default=None, ge=0)
    diagnostic: str | None = None
