from __future__ import annotations

from contextlib import contextmanager
import logging
from collections.abc import Iterator, Mapping

from opentelemetry import trace
from opentelemetry.trace import Span


logger = logging.getLogger("eidos.runtime.telemetry")
TRACER_NAME = "eidos.runtime"


def get_tracer() -> trace.Tracer:
    try:
        return trace.get_tracer(TRACER_NAME)
    except Exception:
        logger.exception("OpenTelemetry tracer lookup failed")
        return trace.NoOpTracerProvider().get_tracer(TRACER_NAME)


def current_span() -> Span:
    try:
        return trace.get_current_span()
    except Exception:
        logger.exception("OpenTelemetry current span lookup failed")
        return trace.INVALID_SPAN


@contextmanager
def start_span(
    name: str,
    *,
    attributes: Mapping[str, object] | None = None,
) -> Iterator[Span]:
    """Start one standard OTel span while containing SDK failures."""

    try:
        manager = get_tracer().start_as_current_span(
            name,
            attributes=attributes,
            record_exception=False,
            set_status_on_exception=False,
        )
        span = manager.__enter__()
    except Exception:
        logger.exception("OpenTelemetry span start failed: %s", name)
        yield current_span()
        return

    error_info: tuple[type[BaseException] | None, BaseException | None, object] = (
        None,
        None,
        None,
    )
    try:
        yield span
    except BaseException as error:
        record_exception(span, error)
        set_span_error(span)
        error_info = (type(error), error, error.__traceback__)
        raise
    finally:
        try:
            manager.__exit__(*error_info)
        except Exception:
            logger.exception("OpenTelemetry span end failed: %s", name)


def set_span_attribute(span: Span, name: str, value: object) -> None:
    if value is None:
        return
    try:
        span.set_attribute(name, value)  # type: ignore[arg-type]
    except Exception:
        logger.exception("OpenTelemetry span attribute failed: %s", name)


def record_exception(span: Span, error: BaseException) -> None:
    try:
        span.record_exception(error)
    except Exception:
        logger.exception("OpenTelemetry exception recording failed")


def set_span_error(span: Span) -> None:
    try:
        span.set_status(trace.Status(trace.StatusCode.ERROR))
    except Exception:
        logger.exception("OpenTelemetry span status failed")


@contextmanager
def run_span(
    run_id: str,
    model_id: str,
    session_id: str | None = None,
) -> Iterator[Span]:
    attributes: dict[str, object] = {
        "eidos.run.id": run_id,
        "eidos.model.id": model_id,
    }
    if session_id is not None:
        attributes["eidos.session.id"] = session_id
    with start_span("eidos.run", attributes=attributes) as span:
        yield span


@contextmanager
def model_attempt_span(
    run_id: str,
    step_id: str,
    model_id: str,
    configured_provider_id: str | None = None,
) -> Iterator[Span]:
    attributes = {
        "eidos.run.id": run_id,
        "eidos.step.id": step_id,
        "eidos.model.id": model_id,
    }
    if configured_provider_id is not None:
        attributes["eidos.model.configured_provider"] = configured_provider_id
    with start_span("eidos.model.attempt", attributes=attributes) as span:
        try:
            yield span
        except BaseException as error:
            set_span_attribute(span, "eidos.model.error.type", type(error).__name__)
            failure = getattr(error, "failure", None)
            if failure is not None:
                set_span_attribute(
                    span,
                    "eidos.model.provider",
                    getattr(failure, "provider_name", None),
                )
                set_span_attribute(
                    span,
                    "eidos.model.error.code",
                    getattr(failure, "code", None),
                )
                set_span_attribute(
                    span,
                    "eidos.model.transport_retry_count",
                    getattr(failure, "transport_retry_count", 0),
                )
            raise


def finish_model_attempt(span: Span, outcome: object) -> None:
    set_span_attribute(span, "eidos.model.provider", getattr(outcome, "provider_name", None))
    set_span_attribute(
        span,
        "eidos.model.resolved_name",
        getattr(outcome, "resolved_model_name", None),
    )
    set_span_attribute(
        span,
        "eidos.model.finish_reason",
        getattr(outcome, "finish_reason", None),
    )
    set_span_attribute(
        span,
        "eidos.model.provider_response_id",
        getattr(outcome, "provider_response_id", None),
    )
    set_span_attribute(
        span,
        "eidos.model.response_state",
        getattr(outcome, "response_state", None),
    )
    phase = getattr(outcome, "phase", None)
    set_span_attribute(
        span,
        "eidos.model.phase",
        phase.value if hasattr(phase, "value") else phase,
    )
    set_span_attribute(
        span,
        "eidos.model.tool_call_count",
        len(getattr(outcome, "tool_calls", ())),
    )
    text = getattr(outcome, "text", "")
    set_span_attribute(
        span,
        "eidos.model.response_text_bytes",
        len(text.encode("utf-8")) if isinstance(text, str) else 0,
    )
    set_span_attribute(span, "eidos.model.ttft_ms", getattr(outcome, "ttft_ms", None))
    set_span_attribute(
        span,
        "eidos.model.duration_ms",
        getattr(outcome, "duration_ms", None),
    )
    set_span_attribute(
        span,
        "eidos.model.transport_retry_count",
        getattr(outcome, "retry_count", 0),
    )
    usage = getattr(outcome, "usage", None)
    if usage is None:
        return
    set_span_attribute(
        span,
        "gen_ai.usage.input_tokens",
        getattr(usage, "input_tokens", None),
    )
    set_span_attribute(
        span,
        "gen_ai.usage.output_tokens",
        getattr(usage, "output_tokens", None),
    )
    set_span_attribute(
        span,
        "gen_ai.usage.cache_read_input_tokens",
        getattr(usage, "cache_read_tokens", None),
    )
    set_span_attribute(
        span,
        "gen_ai.usage.cache_write_input_tokens",
        getattr(usage, "cache_write_tokens", None),
    )


@contextmanager
def tool_call_span(
    run_id: str,
    tool_name: str,
    call_id: str,
) -> Iterator[Span]:
    attributes = {
        "eidos.run.id": run_id,
        "eidos.tool.name": tool_name,
        "eidos.tool.call_id": call_id,
    }
    with start_span("eidos.tool.call", attributes=attributes) as span:
        try:
            yield span
        except BaseException as error:
            set_span_attribute(span, "eidos.tool.error.type", type(error).__name__)
            raise


def finish_tool_call(span: Span, outcome: object) -> None:
    status = getattr(outcome, "tool_status", None)
    set_span_attribute(span, "eidos.tool.status", status)
    set_span_attribute(
        span,
        "eidos.tool.workspace_changed",
        getattr(outcome, "workspace_changed", False),
    )
    if status != "completed":
        set_span_error(span)


def finish_run(span: Span, status: object) -> None:
    set_span_attribute(span, "eidos.run.status", status)
    if status in {"failed", "interrupted"}:
        set_span_error(span)


def record_current_exception(error: BaseException) -> None:
    span = current_span()
    record_exception(span, error)
    set_span_error(span)
