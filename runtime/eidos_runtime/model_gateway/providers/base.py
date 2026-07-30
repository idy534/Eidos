from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from eidos_runtime.model_gateway.usage import NormalizedUsage


class ProviderAdapter(Protocol):
    provider_id: str

    def auth_headers(self, api_key: str) -> dict[str, str]: ...

    def normalize_usage(self, usage: dict[str, object]) -> NormalizedUsage: ...


@dataclass(frozen=True)
class _BearerProvider:
    provider_id: str

    def auth_headers(self, api_key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {api_key}"}

    def normalize_usage(self, usage: dict[str, object]) -> NormalizedUsage:
        input_tokens = _token(usage, "input_tokens", "prompt_tokens")
        output_tokens = _token(usage, "output_tokens", "completion_tokens")
        total_tokens = _token(usage, "total_tokens")
        return NormalizedUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            provider_reported=True,
            estimated=False,
        )


class OpenAIProviderAdapter(_BearerProvider):
    def __init__(self) -> None:
        super().__init__("openai")


class OpenAICompatibleProviderAdapter(_BearerProvider):
    def __init__(self) -> None:
        super().__init__("openai_compatible")


def _token(usage: dict[str, object], *keys: str) -> int | None:
    for key in keys:
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None
