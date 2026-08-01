from __future__ import annotations

from dataclasses import dataclass

import httpx
from openai import AsyncOpenAI
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.providers.moonshotai import MoonshotAIProvider
from pydantic_ai.providers.openai import OpenAIProvider

from eidos_runtime.model.config import (
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    MODEL_CATALOG,
    ModelConfig,
)
from eidos_runtime.model_gateway.retry_transport import (
    RetryTransportClient,
    build_retrying_http_client,
)


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
) -> tuple[OpenAICompatibleProvider, AsyncOpenAI, RetryTransportClient]:
    provider_id = MODEL_CATALOG.provider_id_for(config.id)
    retry_client = build_retrying_http_client(config, timeout=timeout)
    client = AsyncOpenAI(
        api_key=config.api_key,
        base_url=_client_base_url(config.url),
        max_retries=0,
        timeout=timeout,
        http_client=retry_client.http_client,
    )
    if provider_id == "deepseek":
        provider: OpenAICompatibleProvider = DeepSeekProvider(openai_client=client)
    elif provider_id == "kimi":
        provider = MoonshotAIProvider(openai_client=client)
    elif provider_id == "minimax":
        provider = OpenAIProvider(openai_client=client)
    else:
        raise ValueError("unknown model provider")
    return provider, client, retry_client


def build_model(config: ModelConfig, provider: OpenAICompatibleProvider) -> Model:
    return OpenAIChatModel(config.id, provider=provider)


def build_pydantic_model(config: ModelConfig) -> BuiltPydanticModel:
    timeout = httpx.Timeout(
        connect=min(10.0, DEFAULT_REQUEST_TIMEOUT_SECONDS),
        read=DEFAULT_REQUEST_TIMEOUT_SECONDS,
        write=min(30.0, DEFAULT_REQUEST_TIMEOUT_SECONDS),
        pool=min(10.0, DEFAULT_REQUEST_TIMEOUT_SECONDS),
    )
    provider, provider_client, retry_client = build_provider(config, timeout=timeout)
    return BuiltPydanticModel(
        model=build_model(config, provider),
        provider_client=provider_client,
        retry_client=retry_client,
    )


def _client_base_url(endpoint: str) -> str:
    suffix = "/chat/completions"
    if not endpoint.endswith(suffix):
        raise ValueError("model URL must be a Chat Completions endpoint")
    return endpoint[: -len(suffix)].rstrip("/")
