from __future__ import annotations

from eidos_runtime.model_gateway.models import WireAPI
from eidos_runtime.model_gateway.presets import PRESETS
from eidos_runtime.model_gateway.providers.base import (
    AnthropicProviderAdapter,
    OpenAICompatibleProviderAdapter,
    OpenAIProviderAdapter,
    ProviderAdapter,
)
from eidos_runtime.model_gateway.wire.base import (
    AnthropicMessagesWireAdapter,
    OpenAIChatCompletionsWireAdapter,
    OpenAIResponsesWireAdapter,
    WireAdapter,
)


class AdapterRegistry:
    def __init__(
        self,
        providers: dict[str, ProviderAdapter],
        wires: dict[WireAPI, WireAdapter],
    ) -> None:
        self._providers = providers
        self._wires = wires

    @classmethod
    def default(cls) -> AdapterRegistry:
        openai = OpenAIProviderAdapter()
        anthropic = AnthropicProviderAdapter()
        compatible = OpenAICompatibleProviderAdapter()
        providers: dict[str, ProviderAdapter] = {
            "openai": openai,
            "anthropic": anthropic,
            "custom": compatible,
        }
        providers.update({
            preset_id: {
                "openai": openai,
                "anthropic": anthropic,
                "openai_compatible": compatible,
            }[preset.provider_adapter_id]
            for preset_id, preset in PRESETS.items()
        })
        return cls(
            providers,
            {
                WireAPI.OPENAI_RESPONSES: OpenAIResponsesWireAdapter(),
                WireAPI.ANTHROPIC_MESSAGES: AnthropicMessagesWireAdapter(),
                WireAPI.OPENAI_CHAT_COMPLETIONS: OpenAIChatCompletionsWireAdapter(),
            },
        )

    def provider(self, provider: str) -> ProviderAdapter:
        try:
            return self._providers[provider]
        except KeyError:
            raise ValueError("unknown model provider") from None

    def wire(self, wire_api: WireAPI) -> WireAdapter:
        try:
            return self._wires[wire_api]
        except KeyError:
            raise ValueError("unknown model wire API") from None
