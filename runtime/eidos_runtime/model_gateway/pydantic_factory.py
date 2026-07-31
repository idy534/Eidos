from __future__ import annotations

from dataclasses import dataclass

import httpx
from openai import AsyncOpenAI
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.alibaba import AlibabaProvider
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.providers.moonshotai import MoonshotAIProvider
from pydantic_ai.providers.openai import OpenAIProvider

from eidos_runtime.model_gateway.models import ModelProfile, RunModelSnapshot, WireAPI
from eidos_runtime.model_gateway.presets import PRESETS


OpenAICompatibleProvider = (
    OpenAIProvider | DeepSeekProvider | MoonshotAIProvider | AlibabaProvider
)


@dataclass(frozen=True)
class BuiltPydanticModel:
    model: Model
    provider_client: AsyncOpenAI


def build_provider(
    profile: ModelProfile,
    api_key: str,
    *,
    timeout: httpx.Timeout,
) -> tuple[OpenAICompatibleProvider, AsyncOpenAI]:
    """Build a Pydantic AI provider from Eidos's already-frozen configuration."""
    validate_provider_configuration(profile)
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=profile.base_url,
        max_retries=0,
        timeout=timeout,
    )
    match profile.provider:
        case "openai":
            provider = OpenAIProvider(openai_client=client)
        case "deepseek":
            provider = DeepSeekProvider(openai_client=client)
        case "moonshot":
            provider = MoonshotAIProvider(openai_client=client)
        case "qwen":
            provider = AlibabaProvider(openai_client=client)
        case "volcengine_ark" | "minimax" | "custom_openai_compatible":
            provider = OpenAIProvider(openai_client=client)
        case _:
            # PRESETS membership above makes this branch unreachable until a
            # product preset is added without a deliberate Pydantic AI mapping.
            raise ValueError("unknown model provider")
    return provider, client


def validate_provider_configuration(profile: ModelProfile) -> None:
    if profile.provider not in PRESETS:
        raise ValueError("unknown model provider")
    if profile.base_url is None:
        raise ValueError("model base URL is required")


def build_model(snapshot: RunModelSnapshot, provider: OpenAICompatibleProvider) -> Model:
    """Select the public Pydantic AI model class for the frozen WireAPI."""
    match snapshot.profile.wire_api:
        case WireAPI.OPENAI_RESPONSES:
            return OpenAIResponsesModel(snapshot.profile.model_id, provider=provider)
        case WireAPI.OPENAI_CHAT_COMPLETIONS:
            return OpenAIChatModel(snapshot.profile.model_id, provider=provider)
        case _:
            raise ValueError("unknown model wire API")


def build_pydantic_model(snapshot: RunModelSnapshot, api_key: str) -> BuiltPydanticModel:
    profile = snapshot.profile
    timeout = httpx.Timeout(
        connect=min(10.0, profile.request_timeout),
        read=profile.request_timeout,
        write=min(30.0, profile.request_timeout),
        pool=min(10.0, profile.request_timeout),
    )
    provider, provider_client = build_provider(profile, api_key, timeout=timeout)
    return BuiltPydanticModel(
        model=build_model(snapshot, provider),
        provider_client=provider_client,
    )
