from __future__ import annotations

import threading

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import (
    ModelClient,
    ModelRequestError,
    ModelRequestFailure,
)
from eidos_runtime.model_gateway.retry import RetryDecision, RetryState, retry_decision
from eidos_runtime.model_gateway.models import RetryPolicy
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
    """Owns Step sampling and ModelAttempts.

    Assistant text stays provisional until RuntimeEngine validates the complete
    model response. This prevents invalid protocol output and tool-call control
    syntax from becoming persisted conversation facts before validation.
    """

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
        provisional_text: list[str] = []
        try:
            frozen = self.store.read_running_context_snapshot(step.run_id)
            model_context = (
                tuple({"role": message.role, "content": message.content}
                      for message in frozen.plan.messages)
                if frozen is not None
                else step.model_context
            )
            result = self.runner.run(
                model_context,
                cancel,
                provisional_text.append,
                instructions=step.instructions.text,
                tool_definitions=step.tool_definitions,
            )
        except ModelStreamInterrupted as interrupted:
            had_progress = bool(provisional_text or interrupted.text)
            error = _sampling_error(interrupted.cause, had_progress=had_progress)
            if isinstance(error, SensitiveScanError):
                self.store.complete_current_model_attempt(
                    step.run_id,
                    "failed",
                    error_code="sensitive_scan_failed",
                    ttft_ms=interrupted.ttft_ms,
                    duration_ms=interrupted.duration_ms,
                    had_progress=had_progress,
                    retry_decision={"retry": False, "reason": "sensitive_scan_failed"},
                )
                raise error
            failure = error.failure if isinstance(error, SamplingError) else None
            decision = _terminal_retry_decision(
                error,
                failure,
                had_progress=had_progress,
                canceled=isinstance(error, SamplingCancelled),
                max_attempts=step.model_profile.retry_max_attempts,
            )
            self.store.complete_current_model_attempt(
                step.run_id,
                "canceled" if isinstance(error, SamplingCancelled) else "failed",
                provider_name=failure.provider_name if failure else None,
                error_code=failure.code if failure else str(error),
                http_status=failure.status_code if failure else None,
                ttft_ms=interrupted.ttft_ms,
                duration_ms=interrupted.duration_ms,
                had_progress=had_progress,
                retry_decision=_retry_decision_payload(decision, failure),
            )
            raise error

        invalid_completion = (
            result.response_state not in {None, "complete"}
            or result.finish_reason in {"length", "content_filter", "error"}
        )
        if invalid_completion:
            decision = RetryDecision(retry=False, reason="invalid_completion")
            self.store.complete_current_model_attempt(
                step.run_id,
                "failed",
                usage=result.usage,
                provider_name=result.provider_name,
                resolved_model_name=result.resolved_model_name,
                finish_reason=result.finish_reason,
                provider_response_id=result.provider_response_id,
                error_code=result.finish_reason or result.response_state,
                ttft_ms=result.ttft_ms,
                duration_ms=result.duration_ms,
                had_progress=bool(result.text),
                retry_decision=_retry_decision_payload_from_result(decision, result),
            )
            self._check_cancel(cancel)
            raise SamplingProtocolError(
                result.finish_reason or result.response_state or "incomplete_response",
                had_progress=bool(result.text),
            )

        self._check_cancel(cancel)
        return SamplingOutcome(
            text=result.text,
            tool_calls=result.tool_calls,
            assistant_item=None,
            retry_count=result.transport_retry_count,
            usage=result.usage,
            provider_name=result.provider_name,
            resolved_model_name=result.resolved_model_name,
            finish_reason=result.finish_reason,
            provider_response_id=result.provider_response_id,
            response_state=result.response_state,
            ttft_ms=result.ttft_ms,
            duration_ms=result.duration_ms,
        )

    def complete_attempt(
        self,
        step: StepContext,
        sampled: SamplingOutcome,
        *,
        status: str,
        error_code: str | None = None,
        retry: bool = False,
        retry_reason: str = "completed",
    ) -> None:
        self.store.complete_current_model_attempt(
            step.run_id,
            status,
            usage=sampled.usage,
            provider_name=sampled.provider_name,
            resolved_model_name=sampled.resolved_model_name,
            finish_reason=sampled.finish_reason,
            provider_response_id=sampled.provider_response_id,
            error_code=error_code,
            ttft_ms=sampled.ttft_ms,
            duration_ms=sampled.duration_ms,
            had_progress=bool(sampled.text),
            retry_decision={
                "retry": retry,
                "reason": retry_reason,
                "transportAttemptCount": sampled.retry_count + 1,
                "transportRetryCount": sampled.retry_count,
                "lastRetryReason": None,
                "lastBackoffSeconds": None,
                "retryAfterApplied": False,
            },
        )

    def commit_assistant(
        self,
        step: StepContext,
        text: str,
        cancel: threading.Event,
    ) -> dict[str, object] | None:
        if not text:
            return None
        writer = AssistantStreamWriter(
            self.store,
            self.events,
            step.run_id,
            step.step_index,
            check_cancel=lambda: self._check_cancel(cancel),
        )
        writer.write(text)
        writer.flush()
        return writer.item

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


def _terminal_retry_decision(
    error: SamplingError,
    failure: ModelRequestFailure | None,
    *,
    had_progress: bool,
    canceled: bool,
    max_attempts: int,
) -> RetryDecision:
    if had_progress or (failure is None and isinstance(error, SamplingRetryableError)):
        return RetryDecision(retry=False, reason="unsafe_stream_progress")
    decision = retry_decision(
        failure or error,
        RetryState(
            attempt_number=(failure.transport_attempt_count if failure else 1) or 1,
            visible_output_emitted=had_progress,
            canceled=canceled,
        ),
        RetryPolicy(max_attempts=max_attempts),
    )
    if decision.reason == "retry_budget_exhausted":
        return RetryDecision(retry=False, reason="transport_retries_exhausted")
    if decision.retry:
        # Sampling never owns a second transport request loop. If a failure reaches
        # it after a ModelRunner interruption, replay is unsafe by definition.
        return RetryDecision(retry=False, reason="unsafe_stream_progress")
    return decision


def _retry_decision_payload(
    decision: RetryDecision,
    failure: ModelRequestFailure | None,
) -> dict[str, object]:
    return {
        "retry": decision.retry,
        "reason": decision.reason,
        "transportAttemptCount": failure.transport_attempt_count if failure else 0,
        "transportRetryCount": failure.transport_retry_count if failure else 0,
        "lastRetryReason": failure.last_retry_reason if failure else None,
        "lastBackoffSeconds": failure.last_backoff_seconds if failure else None,
        "retryAfterApplied": failure.retry_after_applied if failure else False,
    }


def _retry_decision_payload_from_result(
    decision: RetryDecision,
    result: object,
) -> dict[str, object]:
    return {
        "retry": decision.retry,
        "reason": decision.reason,
        "transportAttemptCount": getattr(result, "transport_attempt_count", 0),
        "transportRetryCount": getattr(result, "transport_retry_count", 0),
        "lastRetryReason": getattr(result, "last_retry_reason", None),
        "lastBackoffSeconds": getattr(result, "last_backoff_seconds", None),
        "retryAfterApplied": getattr(result, "retry_after_applied", False),
    }