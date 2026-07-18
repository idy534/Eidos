from __future__ import annotations

from dataclasses import dataclass, field
import threading
from typing import Callable, Protocol, Sequence


ModelContextItem = dict[str, object]
ModelToolDefinition = dict[str, object]


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
    def generate_title(self, user_input: str, cancel: threading.Event) -> str: ...

    def complete(
        self,
        context: tuple[ModelContextItem, ...],
        cancel: threading.Event,
        on_text_delta: Callable[[str], None],
        allow_tools: bool = True,
        tool_definitions: tuple[ModelToolDefinition, ...] = (),
    ) -> ModelResponse: ...


@dataclass
class ScriptedModel:
    responses: Sequence[ModelResponse]
    generated_title: str = "Fixture task"
    contexts: list[tuple[ModelContextItem, ...]] = field(default_factory=list)
    allow_tools_history: list[bool] = field(default_factory=list)
    tool_definitions_history: list[tuple[ModelToolDefinition, ...]] = field(
        default_factory=list
    )
    title_inputs: list[str] = field(default_factory=list)
    _index: int = 0

    def generate_title(self, user_input: str, cancel: threading.Event) -> str:
        if cancel.is_set():
            return ""
        self.title_inputs.append(user_input)
        return self.generated_title

    def complete(
        self,
        context: tuple[ModelContextItem, ...],
        cancel: threading.Event,
        on_text_delta: Callable[[str], None],
        allow_tools: bool = True,
        tool_definitions: tuple[ModelToolDefinition, ...] = (),
    ) -> ModelResponse:
        if cancel.is_set():
            return ModelResponse()
        self.contexts.append(context)
        self.allow_tools_history.append(allow_tools)
        self.tool_definitions_history.append(tool_definitions)
        if self._index >= len(self.responses):
            return ModelResponse()
        response = self.responses[self._index]
        self._index += 1
        if response.text:
            on_text_delta(response.text)
        return response
