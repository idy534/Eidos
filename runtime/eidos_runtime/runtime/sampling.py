from __future__ import annotations

import threading

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import (
    ModelClient,
    ModelRequestError,
    ModelRequestFailure,
)
from eidos_runtime.runtime.assistant_stream import AssistantStreamWriter
from eidos_runtime.runtime.contracts import SamplingOutcome, StepContext
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.runtime.model_runner import ModelRunner, ModelStreamInterrupted
from eidos_runtime.sandbox.sensitive import SensitiveScanError, SensitiveScanner


class SamplingError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        had_progress: bool = False,
        failure: ModelRequestFailure | None = None,
    ) -> None:
        super().__init__(message)
        self.had_progress = had_progress
        self.failure = failure


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
                if writer.item is not None:
                    writer.abort()
                error = _sampling_error(
                    interrupted.cause, had_progress=writer.item is not None
                )
                if isinstance(error, SensitiveScanError):
                    self.store.complete_current_model_attempt(
                        step.run_id,
                        "failed",
                        error_code="sensitive_scan_failed",
                        ttft_ms=interrupted.ttft_ms,
                        duration_ms=interrupted.duration_ms,
                        had_progress=writer.item is not None,
                    )
                    raise error
                if (
                    isinstance(error, SamplingRetryableError)
                    and retries < step.model_profile.retry_max_attempts - 1
                ):
                    retries += 1
                    self.store.complete_current_model_attempt(
                        step.run_id,
                        "failed",
                        provider_name=(
                            error.failure.provider_name if error.failure else None
                        ),
                        error_code=(error.failure.code if error.failure else str(error)),
                        http_status=(
                            error.failure.status_code if error.failure else None
                        ),
                        ttft_ms=interrupted.ttft_ms,
                        duration_ms=interrupted.duration_ms,
                        had_progress=error.had_progress,
                        retry_decision={
                            "retry": True,
                            "reason": "transient_error",
                        },
                    )
                    backoff = min(
                        step.model_profile.retry_initial_backoff_seconds
                        * 2 ** (retries - 1),
                        step.model_profile.retry_max_backoff_seconds,
                    )
                    if error.failure and error.failure.retry_after_seconds is not None:
                        backoff = max(
                            backoff,
                            error.failure.retry_after_seconds,
                        )
                    if cancel.wait(backoff):
                        raise SamplingCancelled("sampling canceled")
                    self.store.start_retry_model_attempt(step.run_id)
                    continue
                self.store.complete_current_model_attempt(
                    step.run_id,
                    "canceled" if isinstance(error, SamplingCancelled) else "failed",
                    provider_name=(error.failure.provider_name if error.failure else None),
                    error_code=(error.failure.code if error.failure else str(error)),
                    http_status=(error.failure.status_code if error.failure else None),
                    ttft_ms=interrupted.ttft_ms,
                    duration_ms=interrupted.duration_ms,
                    had_progress=error.had_progress,
                    retry_decision={
                        "retry": False,
                        "reason": (
                            "canceled"
                            if isinstance(error, SamplingCancelled)
                            else "non_retryable_or_exhausted"
                        ),
                    },
                )
                raise error

            writer.flush()
            invalid_completion = (
                result.response_state not in {None, "complete"}
                or result.finish_reason in {
                    "length",
                    "content_filter",
                    "error",
                }
            )
            self.store.complete_current_model_attempt(
                step.run_id,
                "failed" if invalid_completion else "completed",
                usage=result.usage,
                provider_name=result.provider_name,
                resolved_model_name=result.resolved_model_name,
                finish_reason=result.finish_reason,
                provider_response_id=result.provider_response_id,
                error_code=(
                    (result.finish_reason or result.response_state)
                    if invalid_completion
                    else None
                ),
                ttft_ms=result.ttft_ms,
                duration_ms=result.duration_ms,
                had_progress=writer.item is not None,
                retry_decision={
                    "retry": False,
                    "reason": (
                        "invalid_completion"
                        if invalid_completion else "completed"
                    ),
                },
            )
            self._check_cancel(cancel)
            if invalid_completion:
                if writer.item is not None:
                    writer.abort()
                raise SamplingProtocolError(
                    result.finish_reason or result.response_state or "incomplete_response",
                    had_progress=writer.item is not None,
                )
            if result.text and writer.item is None:
                writer.write(result.text)
                writer.flush()
            return SamplingOutcome(
                text=result.text,
                tool_calls=result.tool_calls,
                assistant_item=writer.item,
                retry_count=retries,
                usage=result.usage,
                provider_name=result.provider_name,
                resolved_model_name=result.resolved_model_name,
                finish_reason=result.finish_reason,
                provider_response_id=result.provider_response_id,
                response_state=result.response_state,
                ttft_ms=result.ttft_ms,
                duration_ms=result.duration_ms,
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
    if isinstance(error, ModelRequestError):
        failure = error.failure.model_copy(update={
            "had_progress": error.failure.had_progress or had_progress
        })
        options = {"had_progress": failure.had_progress, "failure": failure}
        if failure.code == "sampling_canceled":
            return SamplingCancelled(failure.code, **options)
        if failure.code == "authentication_failed":
            return SamplingAuthenticationFailed(failure.code, **options)
        if failure.code == "rate_limited":
            return SamplingRateLimited(failure.code, **options)
        if failure.code == "context_exceeded":
            return SamplingContextExceeded(failure.code, **options)
        if failure.code == "invalid_request":
            return SamplingInvalidRequest(failure.code, **options)
        if failure.code == "protocol_error":
            return SamplingProtocolError(failure.code, **options)
        if failure.retryable:
            return SamplingRetryableError(failure.code, **options)
        return SamplingInvalidRequest(failure.code, **options)
    if isinstance(error, OSError):
        return SamplingRetryableError(str(error), had_progress=had_progress)
    return SamplingProtocolError(
        str(error or "model request failed"), had_progress=had_progress
    )
