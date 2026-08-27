from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Callable

from eidos_runtime.model.client import (
    AssistantMessagePhase,
    ModelClient,
    ModelContextItem,
    ModelResponse,
    ModelToolCall,
    ModelToolDefinition,
    ModelUsage,
)
from eidos_runtime.sandbox.sensitive import (
    SensitiveScanError,
    SensitiveScanner,
    StreamingSensitiveScanner,
    default_scanner,
)


@dataclass(frozen=True)
class ModelStepResult:
    text: str
    tool_calls: tuple[ModelToolCall, ...]
    phase: AssistantMessagePhase | None = AssistantMessagePhase.UNKNOWN
    usage: ModelUsage | None = None
    provider_name: str | None = None
    resolved_model_name: str | None = None
    finish_reason: str | None = None
    provider_response_id: str | None = None
    response_state: str | None = None
    ttft_ms: int | None = None
    duration_ms: int | None = None
    transport_attempt_count: int = 0
    transport_retry_count: int = 0
    last_retry_reason: str | None = None
    last_backoff_seconds: float | None = None
    retry_after_applied: bool = False


class ModelStreamInterrupted(RuntimeError):
    """The model failed after emitting text that may still be safely persisted."""

    def __init__(
        self,
        text: str,
        cause: Exception | None = None,
        *,
        ttft_ms: int | None = None,
        duration_ms: int | None = None,
    ) -> None:
        self.text = text
        self.cause = cause
        self.ttft_ms = ttft_ms
        self.duration_ms = duration_ms

class ModelRunner:
    """Owns one model invocation and keeps raw stream fragments out of callers."""

    def __init__(
        self,
        model: ModelClient,
        sensitive: SensitiveScanner | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._model = model
        self._sensitive = sensitive or default_scanner()
        self._monotonic = monotonic

    def run(
        self,
        context: tuple[ModelContextItem, ...],
        cancel: threading.Event,
        on_text: Callable[[str], None],
        *,
        instructions: str,
        allow_tools: bool = True,
        tool_definitions: tuple[ModelToolDefinition, ...] = (),
    ) -> ModelStepResult:
        started = self._monotonic()
        first_safe: float | None = None

        def on_safe_text(delta: str) -> None:
            nonlocal first_safe
            if first_safe is None:
                first_safe = self._monotonic()
            on_text(delta)

        stream = StreamingSensitiveScanner(self._sensitive, on_safe_text)
        try:
            response = self._model.complete(
                context,
                cancel,
                stream.feed,
                instructions=instructions,
                allow_tools=allow_tools,
                tool_definitions=tool_definitions,
            )
            text = stream.finish().text
        except Exception as error:
            # Finish the scanner before surfacing the interruption: safe visible
            # progress is retained, while sensitive content still raises normally.
            if not isinstance(error, SensitiveScanError):
                try:
                    stream.finish()
                except Exception as finish_error:
                    error = finish_error
            ended = self._monotonic()
            raise ModelStreamInterrupted(
                "",
                error,
                ttft_ms=(
                    int((first_safe - started) * 1000)
                    if first_safe is not None else None
                ),
                duration_ms=int((ended - started) * 1000),
            ) from error
        ended = self._monotonic()
        if not isinstance(response, ModelResponse):
            return ModelStepResult("", (), duration_ms=int((ended - started) * 1000))
        # Tool-only responses intentionally keep TTFT unset; this is stable and
        # avoids deriving timing from provider-specific partial ToolCall events.
        return ModelStepResult(
            text=text,
            tool_calls=response.tool_calls,
            phase=response.phase,
            usage=response.usage,
            provider_name=response.provider_name,
            resolved_model_name=response.resolved_model_name,
            finish_reason=response.finish_reason,
            provider_response_id=response.provider_response_id,
            response_state=response.response_state,
            ttft_ms=(
                int((first_safe - started) * 1000)
                if first_safe is not None else None
            ),
            duration_ms=int((ended - started) * 1000),
            transport_attempt_count=response.transport_attempt_count,
            transport_retry_count=response.transport_retry_count,
            last_retry_reason=response.last_retry_reason,
            last_backoff_seconds=response.last_backoff_seconds,
            retry_after_applied=response.retry_after_applied,
        )
