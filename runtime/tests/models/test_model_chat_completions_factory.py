from __future__ import annotations

import httpx
import pytest
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.providers.moonshotai import MoonshotAIProvider
from pydantic_ai.providers.openai import OpenAIProvider

from eidos_runtime.model.config import MODEL_CATALOG
from eidos_runtime.model_gateway.pydantic_factory import build_model, build_provider


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider_id", "model_id", "provider_type", "base_url"),
    [
        (
            "deepseek",
            "deepseek-v4-flash",
            DeepSeekProvider,
            "https://api.deepseek.com",
        ),
        (
            "minimax",
            "MiniMax-M3",
            OpenAIProvider,
            "https://api.minimaxi.com/v1",
        ),
        (
            "kimi",
            "kimi-k3",
            MoonshotAIProvider,
            "https://api.moonshot.cn/v1",
        ),
        (
            "volcengine",
            "deepseek-v4-flash-ga-260731",
            OpenAIProvider,
            "https://ark.cn-beijing.volces.com/api/coding/v3",
        ),
    ],
)
async def test_all_catalog_providers_use_openai_chat_completions(
    provider_id: str,
    model_id: str,
    provider_type: type[object],
    base_url: str,
) -> None:
    config = MODEL_CATALOG.materialize(provider_id, model_id, "sk-xxx")
    provider, client, retry_client = build_provider(
        config,
        timeout=httpx.Timeout(30),
    )
    try:
        assert isinstance(provider, provider_type)
        assert str(client.base_url).rstrip("/") == base_url
        model = build_model(config, provider)
        assert isinstance(model, OpenAIChatModel)
        assert model.model_name == model_id
    finally:
        await client.close()
        await retry_client.aclose()
