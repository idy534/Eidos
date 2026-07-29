from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from eidos_runtime.model_gateway.models import WireAPI


class WireAdapter(Protocol):
    wire_api: WireAPI


@dataclass(frozen=True)
class OpenAIResponsesWireAdapter:
    wire_api: WireAPI = WireAPI.OPENAI_RESPONSES


@dataclass(frozen=True)
class AnthropicMessagesWireAdapter:
    wire_api: WireAPI = WireAPI.ANTHROPIC_MESSAGES


@dataclass(frozen=True)
class OpenAIChatCompletionsWireAdapter:
    wire_api: WireAPI = WireAPI.OPENAI_CHAT_COMPLETIONS
