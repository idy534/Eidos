from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from eidos_runtime.models import EidosFrozenStrictModel


class WireAPI(StrEnum):
    OPENAI_CHAT_COMPLETIONS = "openai_chat_completions"


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


class RetryPolicy(EidosFrozenStrictModel):
    max_attempts: int = Field(default=3, ge=1, le=10)
    initial_backoff_seconds: float = Field(default=0.2, ge=0, le=60)
    max_backoff_seconds: float = Field(default=2.0, ge=0, le=300)

    @model_validator(mode="after")
    def validate_backoff(self) -> Self:
        if self.max_backoff_seconds < self.initial_backoff_seconds:
            raise ValueError("max backoff must not be smaller than initial backoff")
        return self
