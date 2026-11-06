"""Desktop's Qoder connection controls. Credentials stay inside the SDK."""
from typing import Literal

from pydantic import Field

from eidos_runtime.models import EidosFrozenStrictModel
from eidos_runtime.model.config import ModelReasoningConfig


class QoderConnectionRequest(EidosFrozenStrictModel):
    action: Literal["status", "check", "login", "refresh"]


class QoderCatalogEntry(EidosFrozenStrictModel):
    id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=120)
    context_windows: tuple[int, ...] = ()
    default_context_window: int | None = None
    max_output_tokens: int | None = None
    supports_images: bool | None = None
    reasoning: ModelReasoningConfig | None = None
    enabled: bool


class QoderConnectionState(EidosFrozenStrictModel):
    resource_preparing: bool = False
    resource_error: str | None = None
    resource_ready: bool
    phase: Literal["idle", "checking", "logging_in", "refreshing", "ready", "error"] = "idle"
    models: tuple[QoderCatalogEntry, ...] = Field(default=(), max_length=256)
    error_code: str | None = None
