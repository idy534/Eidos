from __future__ import annotations

import threading

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import ModelClient
from eidos_runtime.model.deepseek import ModelProviderError
from eidos_runtime.runtime.assistant_stream import AssistantStreamWriter
from eidos_runtime.runtime.contracts import SamplingOutcome, StepContext
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.runtime.model_runner import ModelRunner, ModelStreamInterrupted
from eidos_runtime.sandbox.sensitive import SensitiveScanError, SensitiveScanner


MAX_STREAM_RETRIES = 5


class SamplingError(RuntimeError):
    def __init__(self, message: str, *, had_progress: bool = False) -> None:
        super().__init__(message)
        self.had_progress = had_progress


class SamplingRetryableError(SamplingError):
    pass


class SamplingRateLimited(SamplingRetryableError):
    pass


class SamplingContextExceeded(SamplingError):
    pass


class SamplingAuthenticationFailed(SamplingError):
    pass


class SamplingInvalidRequest(SamplingError):
    pass


class SamplingProtocolError(SamplingError):
    pass


class SamplingCancelled(SamplingError):
    pass


class SamplingRuntime:
    """Owns Step sampling, streamed Assistant Items, and ModelAttempts."""

    def __init__(
        self,
        store: SessionStore,
        model: ModelClient,
        events: RuntimeEvents,
        sensitive: SensitiveScanner,
    ) -> None:
        self.store = store
        self.runner = ModelRunner(model, sensitive)
        self.events = events

    def sample(
        self, step: StepContext, cancel: threading.Event
    ) -> SamplingOutcome:
        retries = 0
        while True:
            writer = AssistantStreamWriter(
                self.store,
                self.events,
                step.run_id,
                step.step_index,
                check_cancel=lambda: self._check_cancel(cancel),
            )

            try:
                result = self.runner.run(
                    step.model_context,
                    cancel,
                    writer.write,
                    tool_definitions=step.tool_definitions,
                )
            except ModelStreamInterrupted as interrupted:
                if interrupted.text:
                    writer.write(interrupted.text)
                writer.flush()
                self._check_cancel(cancel)
                if writer.item is not None:
                    writer.fail()
                error = _sampling_error(
                    interrupted.cause, had_progress=writer.item is not None
                )
                if isinstance(error, SensitiveScanError):
                    raise error
                if isinstance(error, SamplingRetryableError) and retries < MAX_STREAM_RETRIES:
                    retries += 1
                    self.store.retry_current_model_attempt(step.run_id)
                    if cancel.wait(min(0.2 * 2 ** (retries - 1), 2.0)):
                        raise SamplingCancelled("sampling canceled")
                    continue
                raise error

            self._check_cancel(cancel)
            writer.flush()
            if result.text and writer.item is None:
                writer.write(result.text)
                writer.flush()
            return SamplingOutcome(
                text=result.text,
                tool_calls=result.tool_calls,
                assistant_item=writer.item,
                retry_count=retries,
            )

    @staticmethod
    def _check_cancel(cancel: threading.Event) -> None:
        if cancel.is_set():
            raise SamplingCancelled("sampling canceled")


def _sampling_error(
    error: Exception | None, *, had_progress: bool
) -> SamplingError | SensitiveScanError:
    if isinstance(error, SensitiveScanError):
        return error
    if isinstance(error, SamplingError):
        error.had_progress = error.had_progress or had_progress
        return error
    if isinstance(error, ModelProviderError):
        code = str(error)
        if code in {"provider_http_401", "provider_http_403"}:
            return SamplingAuthenticationFailed(code, had_progress=had_progress)
        if code == "provider_http_429":
            return SamplingRateLimited(code, had_progress=had_progress)
        if code in {"provider_http_413", "provider_http_431"}:
            return SamplingContextExceeded(code, had_progress=had_progress)
        if code in {"provider_http_400", "provider_http_404", "provider_http_422"}:
            return SamplingInvalidRequest(code, had_progress=had_progress)
        if code in {
            "provider_protocol_error",
            "provider_invalid_tool_arguments",
            "provider_incomplete_response",
            "provider_response_too_large",
        }:
            return SamplingProtocolError(code, had_progress=had_progress)
        if code in {
            "provider_unavailable",
            "provider_timeout",
            "provider_http_408",
            "provider_http_425",
            "provider_http_500",
            "provider_http_502",
            "provider_http_503",
            "provider_http_504",
        }:
            return SamplingRetryableError(code, had_progress=had_progress)
        return SamplingInvalidRequest(code, had_progress=had_progress)
    if isinstance(error, OSError):
        return SamplingRetryableError(str(error), had_progress=had_progress)
    return SamplingProtocolError(
        str(error or "model request failed"), had_progress=had_progress
    )
