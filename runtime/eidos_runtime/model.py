from __future__ import annotations

from dataclasses import dataclass, field
import threading
from typing import Callable, Protocol, Sequence


ModelContextItem = dict[str, object]


@dataclass(frozen=True)
class ModelToolCall:
    provider_call_id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class ModelResponse:
    text: str = ""
    tool_calls: tuple[ModelToolCall, ...] = ()


class ModelClient(Protocol):
    def complete(
        self,
        context: tuple[ModelContextItem, ...],
        cancel: threading.Event,
        on_text_delta: Callable[[str], None],
        allow_tools: bool = True,
    ) -> ModelResponse: ...


@dataclass
class ScriptedModel:
    responses: Sequence[ModelResponse]
    contexts: list[tuple[ModelContextItem, ...]] = field(default_factory=list)
    allow_tools_history: list[bool] = field(default_factory=list)
    _index: int = 0

    def complete(
        self,
        context: tuple[ModelContextItem, ...],
        cancel: threading.Event,
        on_text_delta: Callable[[str], None],
        allow_tools: bool = True,
    ) -> ModelResponse:
        if cancel.is_set():
            return ModelResponse()
        self.contexts.append(context)
        self.allow_tools_history.append(allow_tools)
        if self._index >= len(self.responses):
            return ModelResponse()
        response = self.responses[self._index]
        self._index += 1
        if response.text:
            on_text_delta(response.text)
        return response
