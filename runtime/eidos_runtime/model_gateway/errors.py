from __future__ import annotations

import re
from typing import Any

from pydantic import Field

from eidos_runtime.models import EidosFrozenStrictModel
from eidos_runtime.model_gateway.models import WireAPI


_SECRET = re.compile(
    r"(?i)(authorization\s*:\s*bearer\s+|api[-_ ]?key[\"']?\s*[:=]\s*[\"']?|sk-)"
    r"[^\s,\"']+"
)


def redact_diagnostic(value: str | None) -> str | None:
    if value is None:
        return None
    return _SECRET.sub("[REDACTED]", value)[:2_000]


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


class EidosModelTransportError(EidosModelError):
    pass


class EidosModelTimeoutError(EidosModelError):
    pass


class EidosModelRateLimitError(EidosModelError):
    pass


class EidosModelAuthenticationError(EidosModelError):
    pass


class EidosModelPermissionError(EidosModelError):
    pass


class EidosModelNotFoundError(EidosModelError):
    pass


class EidosModelInvalidRequestError(EidosModelError):
    pass


class EidosModelContextExceededError(EidosModelError):
    estimated_input_tokens: int | None = None
    configured_context_window: int | None = None
    requested_output_tokens: int | None = None
    provider_reported_maximum: int | None = None
    provider_reported_input_size: int | None = None


class EidosModelStreamInterruptedError(EidosModelError):
    pass


class EidosModelCancelledError(EidosModelError):
    pass


class EidosModelProviderUnavailableError(EidosModelError):
    pass


def normalize_http_error(
    status: int,
    *,
    provider: str,
    wire_api: WireAPI,
    model_id: str,
    attempt_id: str,
    provider_request_id: str | None = None,
    retry_after: float | None = None,
    diagnostic: str | None = None,
    **context: Any,
) -> EidosModelError:
    lowered = (diagnostic or "").lower()
    common = {
        "provider": provider,
        "wire_api": wire_api,
        "model_id": model_id,
        "attempt_id": attempt_id,
        "provider_request_id": provider_request_id,
        "http_status": status,
        "retry_after": retry_after,
        "diagnostic": redact_diagnostic(diagnostic),
    }
    if status == 401:
        kind, code, message, retryable = (
            EidosModelAuthenticationError,
            "MODEL_AUTHENTICATION_FAILED",
            "Model provider authentication failed",
            False,
        )
    elif status == 403:
        kind, code, message, retryable = (
            EidosModelPermissionError,
            "MODEL_PERMISSION_DENIED",
            "Model provider permission denied",
            False,
        )
    elif status == 404:
        kind, code, message, retryable = (
            EidosModelNotFoundError,
            "MODEL_NOT_FOUND",
            "Configured model was not found",
            False,
        )
    elif status == 429:
        kind, code, message, retryable = (
            EidosModelRateLimitError,
            "MODEL_RATE_LIMITED",
            "Model provider rate limit exceeded",
            True,
        )
    elif status == 413 or (
        status in {400, 422}
        and any(marker in lowered for marker in (
            "context length",
            "context_length",
            "maximum context",
            "too many tokens",
        ))
    ):
        return EidosModelContextExceededError(
            code="MODEL_CONTEXT_EXCEEDED",
            message="Model context limit exceeded",
            retryable=False,
            **common,
            **{
                key: value
                for key, value in context.items()
                if key in EidosModelContextExceededError.model_fields
            },
        )
    elif status in {408, 504}:
        kind, code, message, retryable = (
            EidosModelTimeoutError,
            "MODEL_PROVIDER_TIMEOUT",
            "Model provider timed out",
            True,
        )
    elif status in {425, 500, 502, 503}:
        kind, code, message, retryable = (
            EidosModelProviderUnavailableError,
            "MODEL_PROVIDER_UNAVAILABLE",
            "Model provider is temporarily unavailable",
            True,
        )
    else:
        kind, code, message, retryable = (
            EidosModelInvalidRequestError,
            "MODEL_INVALID_REQUEST",
            "Model provider rejected the request",
            False,
        )
    return kind(code=code, message=message, retryable=retryable, **common)
