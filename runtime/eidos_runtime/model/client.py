from __future__ import annotations

from dataclasses import dataclass, field
import threading
from typing import Annotated, Callable, Literal, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field

from eidos_runtime.model.response_phase import AssistantMessagePhase


MAX_FUNCTION_ARGUMENT_BYTES = 64 * 1024
MAX_CUSTOM_TOOL_INPUT_BYTES = 512 * 1024
MAX_TOOL_CALL_ID_BYTES = 256
MAX_TOOL_NAME_BYTES = 256


ModelContextItem = dict[str, object]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, validate_default=True
    )


class FunctionToolDefinition(_FrozenModel):
    kind: Literal["function"] = "function"
    name: str
    description: str
    parameters_json_schema: dict[str, object]


class CustomToolFormat(_FrozenModel):
    type: Literal["grammar"]
    syntax: Literal["lark"]
    definition: str


class CustomToolDefinition(_FrozenModel):
    kind: Literal["custom"] = "custom"
    name: str
    description: str
    format: CustomToolFormat | None = None


ModelToolDefinition = FunctionToolDefinition
ModelToolDefinitionLike = Annotated[
    FunctionToolDefinition | CustomToolDefinition,
    Field(discriminator="kind"),
]


class FunctionToolPayload(_FrozenModel):
    kind: Literal["function"] = "function"
    arguments: dict[str, object]


class CustomToolPayload(_FrozenModel):
    kind: Literal["custom"] = "custom"
    input: str


ToolPayload = Annotated[
    FunctionToolPayload | CustomToolPayload,
    Field(discriminator="kind"),
]


class ModelToolCall(_FrozenModel):
    provider_call_id: str
    name: str
    payload: ToolPayload

    def __init__(
        self,
        provider_call_id: str,
        name: str,
        payload: ToolPayload | dict[str, object] | None = None,
        *,
        arguments: dict[str, object] | None = None,
    ) -> None:
        if payload is None:
            if arguments is None:
                raise TypeError("payload or arguments is required")
            payload = FunctionToolPayload(arguments=arguments)
        elif arguments is not None:
            raise TypeError("payload and arguments are mutually exclusive")
        elif isinstance(payload, dict):
            if payload.get("kind") == "custom" and set(payload) <= {"kind", "input"}:
                payload = CustomToolPayload.model_validate(payload)
            elif payload.get("kind") == "function" and set(payload) <= {"kind", "arguments"}:
                payload = FunctionToolPayload.model_validate(payload)
            else:
                payload = FunctionToolPayload(arguments=payload)
        super().__init__(
            provider_call_id=provider_call_id,
            name=name,
            payload=payload,
        )

    @property
    def arguments(self) -> dict[str, object]:
        if not isinstance(self.payload, FunctionToolPayload):
            raise AttributeError("custom tool calls do not have arguments")
        return self.payload.arguments

    @property
    def payload_kind(self) -> Literal["function", "custom"]:
        return self.payload.kind


class ModelUsage(_FrozenModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cache_read_tokens: int | None = Field(default=None, ge=0)
    cache_write_tokens: int | None = Field(default=None, ge=0)
    details: dict[str, int] = Field(default_factory=dict)


class ModelResponse(_FrozenModel):
    text: str = ""
    tool_calls: tuple[ModelToolCall, ...] = ()
    phase: AssistantMessagePhase | None = AssistantMessagePhase.UNKNOWN
    usage: ModelUsage | None = None
    provider_name: str | None = None
    resolved_model_name: str | None = None
    finish_reason: str | None = None
    provider_response_id: str | None = None
    response_state: str | None = None
    transport_attempt_count: int = Field(default=0, ge=0)
    transport_retry_count: int = Field(default=0, ge=0)
    last_retry_reason: str | None = None
    last_backoff_seconds: float | None = Field(default=None, ge=0)
    retry_after_applied: bool = False


class ModelRequestFailure(_FrozenModel):
    code: str
    retryable: bool
    status_code: int | None = None
    retry_after_seconds: float | None = Field(default=None, ge=0)
    provider_name: str | None = None
    had_progress: bool = False
    transport_attempt_count: int = Field(default=0, ge=0)
    transport_retry_count: int = Field(default=0, ge=0)
    last_retry_reason: str | None = None
    last_backoff_seconds: float | None = Field(default=None, ge=0)
    retry_after_applied: bool = False


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
    supports_images: bool = False
    supports_custom_tools: bool = False
    supports_tool_grammar: bool = False
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
        *,
        instructions: str,
        allow_tools: bool = True,
        tool_definitions: tuple[ModelToolDefinitionLike, ...] = (),
    ) -> ModelResponse: ...


@dataclass
class ScriptedModel:
    responses: Sequence[ModelResponse]
    generated_title: str = "Fixture task"
    contexts: list[tuple[ModelContextItem, ...]] = field(default_factory=list)
    instructions_history: list[str] = field(default_factory=list)
    allow_tools_history: list[bool] = field(default_factory=list)
    tool_definitions_history: list[tuple[ModelToolDefinitionLike, ...]] = field(
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
        *,
        instructions: str,
        allow_tools: bool = True,
        tool_definitions: tuple[ModelToolDefinitionLike, ...] = (),
    ) -> ModelResponse:
        if cancel.is_set():
            return ModelResponse()
        self.contexts.append(context)
        self.instructions_history.append(instructions)
        self.allow_tools_history.append(allow_tools)
        self.tool_definitions_history.append(tool_definitions)
        if self._index >= len(self.responses):
            return ModelResponse()
        response = self.responses[self._index]
        self._index += 1
        if response.text:
            on_text_delta(response.text)
        return response
