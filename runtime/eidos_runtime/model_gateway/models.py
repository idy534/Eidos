from __future__ import annotations

from datetime import datetime
from enum import StrEnum
import ipaddress
from typing import Self
from urllib.parse import urlparse
import uuid

from pydantic import Field, field_validator, model_validator

from eidos_runtime.models import EidosFrozenStrictModel


class WireAPI(StrEnum):
    OPENAI_RESPONSES = "openai_responses"
    OPENAI_CHAT_COMPLETIONS = "openai_chat_completions"


class ReasoningMode(StrEnum):
    NONE = "none"
    NATIVE = "native"
    COMPATIBLE = "compatible"


class ReasoningEffort(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RetryClassification(StrEnum):
    TRANSPORT = "transport_error"
    CONNECTION_RESET = "connection_reset"
    STREAM_INTERRUPTED = "stream_interrupted"
    RATE_LIMITED = "rate_limited"
    PROVIDER_OVERLOADED = "provider_overloaded"
    PROVIDER_TIMEOUT = "provider_timeout"
    REQUEST_TIMEOUT = "request_timeout"
    AUTHENTICATION = "authentication_error"
    PERMISSION = "permission_error"
    INVALID_REQUEST = "invalid_request"
    CONTEXT_EXCEEDED = "context_exceeded"
    TOOL_SCHEMA_REJECTED = "tool_schema_rejected"
    MODEL_NOT_FOUND = "model_not_found"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown_provider_error"


class CapabilityProbeSource(StrEnum):
    """Capability source values; ACTIVE_PROBE remains parse-only legacy data."""

    PROVIDER_METADATA = "provider_metadata"
    ACTIVE_PROBE = "active_probe"
    BUILT_IN_PRESET = "built_in_preset"
    USER_DECLARATION = "user_declaration"
    CONSERVATIVE_DEFAULT = "conservative_default"


class RetryPolicy(EidosFrozenStrictModel):
    max_attempts: int = Field(default=3, ge=1, le=10)
    initial_backoff_seconds: float = Field(default=0.2, ge=0, le=60)
    max_backoff_seconds: float = Field(default=2.0, ge=0, le=300)

    @model_validator(mode="after")
    def validate_backoff(self) -> Self:
        if self.max_backoff_seconds < self.initial_backoff_seconds:
            raise ValueError("max backoff must not be smaller than initial backoff")
        return self


class ModelProfile(EidosFrozenStrictModel):
    schema_version: int = 1
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=120)
    provider: str = Field(min_length=1, max_length=64)
    base_url: str | None = None
    auth_reference: str = Field(min_length=1, max_length=256)
    wire_api: WireAPI
    model_id: str = Field(min_length=1, max_length=256)
    context_window: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    reasoning_mode: ReasoningMode = ReasoningMode.NONE
    reasoning_effort: ReasoningEffort | None = None
    supports_tools: bool | None = None
    supports_parallel_tools: bool | None = None
    supports_images: bool | None = None
    supports_structured_output: bool | None = None
    supports_prompt_cache: bool | None = None
    request_timeout: float = Field(default=120.0, gt=0, le=600)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    created_at: datetime
    updated_at: datetime

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username:
            raise ValueError("base URL must be an HTTPS origin")
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            address = None
        if address is not None and (
            address.is_loopback
            or address.is_private
            or address.is_link_local
            or address.is_unspecified
        ):
            raise ValueError("base URL must not target a local network")
        return value.rstrip("/")

    @field_validator("auth_reference")
    @classmethod
    def validate_auth_reference(cls, value: str) -> str:
        if not value.startswith(("env:", "local:")):
            raise ValueError("auth reference must be indirect")
        reference = value.split(":", 1)[1]
        if not reference or any(character.isspace() for character in reference):
            raise ValueError("auth reference is invalid")
        return value


class CapabilityWarning(EidosFrozenStrictModel):
    code: str
    capability: str | None = None
    message: str
    source: CapabilityProbeSource


class CapabilitySnapshot(EidosFrozenStrictModel):
    """Declared capability resolution with legacy persistence compatibility fields."""

    schema_version: int = 1
    id: str
    profile_id: str
    provider: str
    wire_api: WireAPI
    model_id: str
    reachable: bool
    authenticated: bool
    context_window: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    supports_tools: bool
    supports_parallel_tools: bool
    supports_images: bool
    supports_structured_output: bool
    supports_prompt_cache: bool
    reasoning_mode: ReasoningMode
    supported_reasoning_efforts: tuple[ReasoningEffort, ...] = ()
    probe_source: CapabilityProbeSource
    probe_version: str
    probed_at: datetime
    warnings: tuple[CapabilityWarning, ...] = ()
    sources: dict[str, CapabilityProbeSource] = Field(default_factory=dict)

class RunModelSnapshot(EidosFrozenStrictModel):
    schema_version: int = 1
    lease_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    profile: ModelProfile
    capability: CapabilitySnapshot
    frozen_at: datetime

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if (
            self.profile.id != self.capability.profile_id
            or self.profile.provider != self.capability.provider
            or self.profile.wire_api != self.capability.wire_api
            or self.profile.model_id != self.capability.model_id
        ):
            raise ValueError("profile and capability snapshot identities differ")
        return self
