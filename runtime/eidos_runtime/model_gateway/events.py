from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from eidos_runtime.model_gateway.errors import EidosModelError
from eidos_runtime.model_gateway.usage import NormalizedUsage


class GatewayEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    run_id: str
    attempt_id: str
    response_id: str | None = None
    sequence: int = Field(ge=0)
    timestamp: datetime


class ModelAttemptStarted(GatewayEvent):
    type: Literal["attempt_started"] = "attempt_started"


class ModelResponseStarted(GatewayEvent):
    type: Literal["response_started"] = "response_started"


class ModelTextDelta(GatewayEvent):
    type: Literal["text_delta"] = "text_delta"
    text: str


class ModelReasoningDelta(GatewayEvent):
    type: Literal["reasoning_delta"] = "reasoning_delta"
    text: str


class ModelToolCallStarted(GatewayEvent):
    type: Literal["tool_call_started"] = "tool_call_started"
    tool_call_id: str
    name: str | None = None


class ModelToolCallArgumentsDelta(GatewayEvent):
    type: Literal["tool_call_arguments_delta"] = "tool_call_arguments_delta"
    tool_call_id: str
    arguments_delta: str


class ModelToolCallCompleted(GatewayEvent):
    type: Literal["tool_call_completed"] = "tool_call_completed"
    tool_call_id: str
    name: str
    arguments: dict[str, object]


class ModelUsageUpdated(GatewayEvent):
    type: Literal["usage_updated"] = "usage_updated"
    usage: NormalizedUsage


class ModelResponseCompleted(GatewayEvent):
    type: Literal["response_completed"] = "response_completed"
    finish_reason: str
    usage: NormalizedUsage | None = None


class ModelResponseInterrupted(GatewayEvent):
    type: Literal["response_interrupted"] = "response_interrupted"
    error: EidosModelError


class ModelAttemptFailed(GatewayEvent):
    type: Literal["attempt_failed"] = "attempt_failed"
    error: EidosModelError


class ModelAttemptCancelled(GatewayEvent):
    type: Literal["attempt_cancelled"] = "attempt_cancelled"
    error: EidosModelError
