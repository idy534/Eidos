from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Callable

from eidos_runtime.model.client import (
    ModelClient,
    ModelContextItem,
    ModelResponse,
    ModelToolCall,
    ModelToolDefinition,
)
from eidos_runtime.sandbox.sensitive import (
    SensitiveScanner,
    StreamingSensitiveScanner,
    default_scanner,
)


@dataclass(frozen=True)
class ModelStepResult:
    text: str
    tool_calls: tuple[ModelToolCall, ...]


class ModelStreamInterrupted(RuntimeError):
    """The model failed after emitting text that may still be safely persisted."""

    def __init__(self, text: str, cause: Exception | None = None) -> None:
        self.text = text
        self.cause = cause

class ModelRunner:
    """Owns one model invocation and keeps raw stream fragments out of callers."""

    def __init__(self, model: ModelClient, sensitive: SensitiveScanner | None = None) -> None:
        self._model = model
        self._sensitive = sensitive or default_scanner()

    def run(
        self,
        context: tuple[ModelContextItem, ...],
        cancel: threading.Event,
        on_text: Callable[[str], None],
        *,
        allow_tools: bool = True,
        tool_definitions: tuple[ModelToolDefinition, ...] = (),
    ) -> ModelStepResult:
        stream = StreamingSensitiveScanner(self._sensitive, on_text)
        try:
            response = self._model.complete(
                context,
                cancel,
                stream.feed,
                allow_tools=allow_tools,
                tool_definitions=tool_definitions,
            )
        except Exception as error:
            # Finish the scanner before surfacing the interruption: safe visible
            # progress is retained, while sensitive content still raises normally.
            stream.finish()
            raise ModelStreamInterrupted("", error) from error
        text = stream.finish().text
        if not isinstance(response, ModelResponse):
            return ModelStepResult("", ())
        return ModelStepResult(text, response.tool_calls)
