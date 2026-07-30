from __future__ import annotations

from dataclasses import dataclass, field
import threading
from typing import Callable, Literal, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field


ModelContextItem = dict[str, object]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class ModelToolDefinition(_FrozenModel):
    name: str
    description: str
    parameters_json_schema: dict[str, object]


class ModelToolCall(_FrozenModel):
    provider_call_id: str
    name: str
    arguments: dict[str, object]

    def __init__(
        self,
        provider_call_id: str,
        name: str,
        arguments: dict[str, object],
    ) -> None:
        super().__init__(
            provider_call_id=provider_call_id,
            name=name,
            arguments=arguments,
        )


class ModelUsage(_FrozenModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cache_read_tokens: int | None = Field(default=None, ge=0)
    cache_write_tokens: int | None = Field(default=None, ge=0)
    details: dict[str, int] = Field(default_factory=dict)


class ModelResponse(_FrozenModel):
    text: str = ""
    tool_calls: tuple[ModelToolCall, ...] = ()
    usage: ModelUsage | None = None
    provider_name: str | None = None
    resolved_model_name: str | None = None
    finish_reason: str | None = None
    provider_response_id: str | None = None
    response_state: str | None = None


class ModelRequestFailure(_FrozenModel):
    code: str
    retryable: bool
    status_code: int | None = None
    retry_after_seconds: float | None = Field(default=None, ge=0)
    provider_name: str | None = None
    had_progress: bool = False


class ModelRequestError(RuntimeError):
    def __init__(self, failure: ModelRequestFailure) -> None:
        self.failure = failure
        super().__init__(failure.code)


class ModelProfileSnapshot(_FrozenModel):
    schema_version: int = 1
    provider_id: str
    model_id: str
    wire_api: Literal[
        "chat_completions",
        "openai_responses",
    ] = "chat_completions"
    context_window_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    request_timeout_seconds: float = Field(gt=0)
    supports_tools: bool
    supports_json_schema_output: bool
    supports_reasoning: bool
    retry_max_attempts: int = Field(default=6, ge=1, le=10)
    retry_initial_backoff_seconds: float = Field(default=0.2, ge=0, le=60)
    retry_max_backoff_seconds: float = Field(default=2.0, ge=0, le=300)
    pydantic_ai_version: str = "2.13.0"


class ModelClient(Protocol):
    @property
    def profile_snapshot(self) -> ModelProfileSnapshot: ...

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
