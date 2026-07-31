from __future__ import annotations

from pydantic import Field

from eidos_runtime.models import EidosFrozenStrictModel
from eidos_runtime.model_gateway.models import WireAPI


class ProviderPreset(EidosFrozenStrictModel):
    id: str
    display_name: str
    default_wire_api: WireAPI
    default_base_url: str | None
    model_id: None = None
    capability_hints: dict[str, bool] = Field(default_factory=dict)
    context_window: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    compatibility_flags: tuple[str, ...] = ()


PRESETS = {
    "openai": ProviderPreset(
        id="openai",
        display_name="OpenAI",
        default_wire_api=WireAPI.OPENAI_RESPONSES,
        default_base_url="https://api.openai.com/v1",
    ),
    "deepseek": ProviderPreset(
        id="deepseek",
        display_name="DeepSeek",
        default_wire_api=WireAPI.OPENAI_CHAT_COMPLETIONS,
        default_base_url="https://api.deepseek.com",
    ),
    "volcengine_ark": ProviderPreset(
        id="volcengine_ark",
        display_name="火山方舟",
        default_wire_api=WireAPI.OPENAI_CHAT_COMPLETIONS,
        default_base_url="https://ark.cn-beijing.volces.com/api/v3",
    ),
    "minimax": ProviderPreset(
        id="minimax",
        display_name="MiniMax",
        default_wire_api=WireAPI.OPENAI_CHAT_COMPLETIONS,
        default_base_url="https://api.minimax.chat/v1",
    ),
    "moonshot": ProviderPreset(
        id="moonshot",
        display_name="Kimi / Moonshot",
        default_wire_api=WireAPI.OPENAI_CHAT_COMPLETIONS,
        default_base_url="https://api.moonshot.cn/v1",
    ),
    "qwen": ProviderPreset(
        id="qwen",
        display_name="Qwen / DashScope",
        default_wire_api=WireAPI.OPENAI_CHAT_COMPLETIONS,
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    ),
    "custom_openai_compatible": ProviderPreset(
        id="custom_openai_compatible",
        display_name="Custom OpenAI-compatible",
        default_wire_api=WireAPI.OPENAI_CHAT_COMPLETIONS,
        default_base_url=None,
    ),
}
