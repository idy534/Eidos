from __future__ import annotations

from typing import Literal

from pydantic import Field, StrictInt

from eidos_runtime.models import EidosFrozenStrictModel, JSON_SAFE_INTEGER_MAX


ModelRetryCause = Literal[
    "connection_failed",
    "timeout",
    "service_unavailable",
    "rate_limited",
    "stream_interrupted",
]
ModelRetryStatus = Literal["waiting", "requesting", "recovered", "failed"]

RETRYABLE_STATUSES_TIMEOUT = frozenset({408, 504})
RETRYABLE_STATUSES_UNAVAILABLE = frozenset({425, 500, 502, 503})


class ModelRetryActivity(EidosFrozenStrictModel):
    """Display projection of one provider request that is being retried.

    The request group is the first failed Attempt; the SQLite model_attempts rows
    stay the only retry authority and this model never drives retry decisions.
    """

    request_group_id: str = Field(min_length=1, max_length=80)
    run_id: str = Field(min_length=1, max_length=80)
    event_id: StrictInt = Field(ge=0, le=JSON_SAFE_INTEGER_MAX)
    started_at: StrictInt = Field(ge=0, le=JSON_SAFE_INTEGER_MAX)
    updated_at: StrictInt = Field(ge=0, le=JSON_SAFE_INTEGER_MAX)
    status: ModelRetryStatus
    retry_index: StrictInt = Field(ge=0, le=64)
    cause: ModelRetryCause
    http_status: StrictInt | None = Field(default=None, ge=100, le=599)
    retry_after_applied: bool = False
    next_retry_at: StrictInt | None = Field(default=None, ge=0, le=JSON_SAFE_INTEGER_MAX)
    deadline_at: StrictInt | None = Field(default=None, ge=0, le=JSON_SAFE_INTEGER_MAX)
    provider_id: str | None = Field(default=None, min_length=1, max_length=128)
    model_id: str | None = Field(default=None, min_length=1, max_length=128)


def classify_retry_cause(
    code: str | None,
    reason: str | None,
    http_status: int | None,
) -> ModelRetryCause:
    """Map the stable failure facts of a retryable provider error to one display cause."""
    if code == "rate_limited" or http_status == 429:
        return "rate_limited"
    if reason == "stream_interrupted" or code == "model_stream_interrupted":
        return "stream_interrupted"
    if code == "provider_timeout" or http_status in RETRYABLE_STATUSES_TIMEOUT:
        return "timeout"
    if http_status in RETRYABLE_STATUSES_UNAVAILABLE:
        return "service_unavailable"
    return "connection_failed"
