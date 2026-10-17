from __future__ import annotations

from dataclasses import dataclass

import httpx2 as httpx
from openai import AsyncOpenAI
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.providers.moonshotai import MoonshotAIProvider
from pydantic_ai.providers.openai import OpenAIProvider

from eidos_runtime.model.config import (
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    MODEL_CATALOG,
    ModelConfig,
)
from eidos_runtime.model.retry_transport import (
    RetryTransportClient,
    build_retrying_http_client,
)
from eidos_runtime.model.gateway_types import RetryPolicy


OpenAICompatibleProvider = OpenAIProvider | DeepSeekProvider | MoonshotAIProvider


@dataclass(frozen=True)
class BuiltPydanticModel:
    model: Model
    provider_client: AsyncOpenAI
    retry_client: RetryTransportClient


def build_provider(
    config: ModelConfig,
    *,
    timeout: httpx.Timeout,
    wire_api: str = "chat_completions",
    retry_policy: RetryPolicy | None = None,
) -> tuple[OpenAICompatibleProvider, AsyncOpenAI, RetryTransportClient]:
    provider_id = "custom" if config.custom else MODEL_CATALOG.provider_id_for(config.id, vendor=config.vendor)
    retry_client = build_retrying_http_client(
        config, timeout=timeout, wire_api=wire_api, retry_policy=retry_policy
    )
    if config.custom is not None and not config.api_key:
        # The SDK requires an internal key. The mature HTTP client's request hook
        # removes its placeholder before every network attempt, including titles.
        retry_client.http_client.event_hooks["request"].append(_omit_authentication)
    client = AsyncOpenAI(
        api_key=config.api_key or "unused-no-auth",
        **({"organization": "", "project": "", "admin_api_key": ""}
           if config.custom else {}),
        base_url=_client_base_url(config.url),
        max_retries=0,
        timeout=timeout,
        http_client=retry_client.http_client,
    )
    if provider_id == "deepseek" or (config.custom is not None and config.custom.reasoning_format == "deepseek"):
        provider: OpenAICompatibleProvider = DeepSeekProvider(openai_client=client)
    elif provider_id == "kimi":
        provider = MoonshotAIProvider(openai_client=client)
    elif provider_id in {"minimax", "volcengine"}:
        provider = OpenAIProvider(openai_client=client)
    else:
        provider = OpenAIProvider(openai_client=client)
    return provider, client, retry_client


async def _omit_authentication(request: httpx.Request) -> None:
    request.headers.pop("Authorization", None)


def build_model(config: ModelConfig, provider: OpenAICompatibleProvider) -> Model:
    if config.custom:
        profile = OpenAIModelProfile({
            **(provider.model_profile(config.model_id or config.id) or {}),
            "openai_chat_supports_max_completion_tokens": config.custom.output_token_field == "max_completion_tokens",
            "openai_system_prompt_role": "system",
        })
        return OpenAIChatModel(config.model_id or config.id, provider=provider, profile=profile)
    return OpenAIChatModel(config.model_id or config.id, provider=provider)


def build_pydantic_model(
    config: ModelConfig,
    *,
    wire_api: str = "chat_completions",
    retry_policy: RetryPolicy | None = None,
) -> BuiltPydanticModel:
    timeout = httpx.Timeout(
        connect=min(10.0, DEFAULT_REQUEST_TIMEOUT_SECONDS),
        read=DEFAULT_REQUEST_TIMEOUT_SECONDS,
        write=min(30.0, DEFAULT_REQUEST_TIMEOUT_SECONDS),
        pool=min(10.0, DEFAULT_REQUEST_TIMEOUT_SECONDS),
    )
    provider, provider_client, retry_client = build_provider(
        config, timeout=timeout, wire_api=wire_api, retry_policy=retry_policy
    )
    return BuiltPydanticModel(
        model=build_model(config, provider),
        provider_client=provider_client,
        retry_client=retry_client,
    )


def _client_base_url(endpoint: str) -> str:
    for suffix in ("/chat/completions", "/responses"):
        if endpoint.endswith(suffix):
            return endpoint[: -len(suffix)].rstrip("/")
    raise ValueError("model URL must be a Chat Completions or Responses endpoint")
