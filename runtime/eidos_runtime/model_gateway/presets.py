from __future__ import annotations

from eidos_runtime.models import EidosFrozenStrictModel
from eidos_runtime.model_gateway.models import WireAPI


class ProviderPreset(EidosFrozenStrictModel):
    id: str
    display_name: str
    provider_adapter_id: str
    default_wire_api: WireAPI
    default_base_url: str | None
    auth_style: str = "bearer"
    model_id: None = None
    capability_hints: dict[str, bool] = {}
    compatibility_flags: tuple[str, ...] = ()


PRESETS = {
    "openai": ProviderPreset(
        id="openai",
        display_name="OpenAI",
        provider_adapter_id="openai",
        default_wire_api=WireAPI.OPENAI_RESPONSES,
        default_base_url="https://api.openai.com/v1",
    ),
    "deepseek": ProviderPreset(
        id="deepseek",
        display_name="DeepSeek",
        provider_adapter_id="openai_compatible",
        default_wire_api=WireAPI.OPENAI_CHAT_COMPLETIONS,
        default_base_url="https://api.deepseek.com",
    ),
    "volcengine_ark": ProviderPreset(
        id="volcengine_ark",
        display_name="火山方舟",
        provider_adapter_id="openai_compatible",
        default_wire_api=WireAPI.OPENAI_CHAT_COMPLETIONS,
        default_base_url="https://ark.cn-beijing.volces.com/api/v3",
    ),
    "minimax": ProviderPreset(
        id="minimax",
        display_name="MiniMax",
        provider_adapter_id="openai_compatible",
        default_wire_api=WireAPI.OPENAI_CHAT_COMPLETIONS,
        default_base_url="https://api.minimax.chat/v1",
    ),
    "moonshot": ProviderPreset(
        id="moonshot",
        display_name="Kimi / Moonshot",
        provider_adapter_id="openai_compatible",
        default_wire_api=WireAPI.OPENAI_CHAT_COMPLETIONS,
        default_base_url="https://api.moonshot.cn/v1",
    ),
    "qwen": ProviderPreset(
        id="qwen",
        display_name="Qwen / DashScope",
        provider_adapter_id="openai_compatible",
        default_wire_api=WireAPI.OPENAI_CHAT_COMPLETIONS,
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    ),
    "custom_openai_compatible": ProviderPreset(
        id="custom_openai_compatible",
        display_name="Custom OpenAI-compatible",
        provider_adapter_id="openai_compatible",
        default_wire_api=WireAPI.OPENAI_CHAT_COMPLETIONS,
        default_base_url=None,
    ),
}
