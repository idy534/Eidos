"""Versioned SDK-worker contract. The pack builder copies this one source.

This module must depend only on the standard library and Pydantic. The SDK
worker imports its copy without importing the Eidos Runtime or its MCP SDK.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

SDK_VERSION = "1.0.17"
CLI_VERSION = "1.1.79"
PACK_ID = "qoder"
RESOURCE_VERSION = "1.0.17.2"
MAX_FRAME_BYTES = 8 * 1024 * 1024
MAX_TEXT_BYTES = 4 * 1024 * 1024


class BridgeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class WorkerTool(BridgeModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(max_length=32 * 1024)
    schema_: dict[str, JsonValue] = Field(alias="schema")


class WorkerRequest(BridgeModel):
    version: Literal[1] = 1
    request_id: str = Field(min_length=1, max_length=64)
    operation: Literal["sample", "catalog", "login"]
    model_id: str = Field(default="", max_length=256)
    context_window_tokens: int = Field(default=128000, gt=8192, le=100000000)
    reasoning_effort: str | None = None
    images: tuple[dict[str, str], ...] = Field(default=(), max_length=16)
    prompt: str = ""
    instructions: str = ""
    tools: tuple[WorkerTool, ...] = Field(default=(), max_length=256)
    max_output_tokens: int = Field(default=8192, gt=0, le=131072)


class WorkerCall(BridgeModel):
    id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=256)
    arguments: dict[str, JsonValue]


class CatalogModel(BridgeModel):
    id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=120)
    enabled: bool
    context_windows: tuple[int, ...] = ()
    default_context_window: int | None = None
    max_output_tokens: int | None = None
    supports_images: bool | None = None
    reasoning_selections: tuple[str, ...] = ()
    default_reasoning: str | None = None


class WorkerText(BridgeModel):
    type: Literal["text"] = "text"
    text: str


class WorkerResult(BridgeModel):
    type: Literal["result"] = "result"
    calls: tuple[WorkerCall, ...] = Field(default=(), max_length=256)
    finish_reason: Literal["end_turn", "tool_use"]
    credits: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    input_tokens: int | None = Field(default=None, gt=0)
    output_tokens: int | None = Field(default=None, gt=0)
    response_id: str | None = Field(default=None, max_length=256)


class WorkerCatalog(BridgeModel):
    type: Literal["catalog"] = "catalog"
    models: tuple[CatalogModel, ...] = Field(max_length=256)


class WorkerLogin(BridgeModel):
    type: Literal["login"] = "login"
    # The CLI opens the system browser itself. Credentials never cross stdio.
    succeeded: bool


class WorkerError(BridgeModel):
    credits: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    type: Literal["error"] = "error"
    code: Literal["authentication_failed", "invalid_request", "rate_limited",
                  "provider_unavailable", "provider_timeout", "context_exceeded", "protocol_error"]
    reason: str = Field(max_length=128)


WorkerPayload = Annotated[
    WorkerText | WorkerResult | WorkerCatalog | WorkerLogin | WorkerError,
    Field(discriminator="type"),
]


class WorkerEvent(BridgeModel):
    version: Literal[1] = 1
    request_id: str = Field(min_length=1, max_length=64)
    payload: WorkerPayload


EVENT_ADAPTER = TypeAdapter(WorkerEvent)
