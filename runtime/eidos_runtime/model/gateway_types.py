from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from eidos_runtime.models import EidosFrozenStrictModel


class WireAPI(StrEnum):
    OPENAI_CHAT_COMPLETIONS = "openai_chat_completions"
    OPENAI_RESPONSES = "openai_responses"


class RetryPolicy(EidosFrozenStrictModel):
    max_attempts: int = Field(default=3, ge=1, le=10)
    initial_backoff_seconds: float = Field(default=0.2, ge=0, le=60)
    max_backoff_seconds: float = Field(default=2.0, ge=0, le=300)
    recovery_window_seconds: float = Field(default=180, gt=0, le=3600)

    @model_validator(mode="after")
    def validate_backoff(self) -> Self:
        if self.max_backoff_seconds < self.initial_backoff_seconds:
            raise ValueError("max backoff must not be smaller than initial backoff")
        return self
