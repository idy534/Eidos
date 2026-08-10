from __future__ import annotations

from dataclasses import dataclass, field
import logging
import os
import sys
import threading
from collections.abc import Iterable

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanProcessor

from eidos_runtime import __version__


logger = logging.getLogger("eidos.runtime.telemetry")

_DEFAULT_SERVICE_NAME = "eidos-runtime"
_SUPPORTED_EXPORTERS = frozenset({"console", "otlp"})


@dataclass
class TelemetryProvider:
    """Process-level SDK lifecycle owned by the Runtime entry point."""

    tracer_provider: TracerProvider | None
    _closed: bool = field(default=False, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def force_flush(self) -> None:
        with self._lock:
            if self._closed or self.tracer_provider is None:
                return
            try:
                flushed = self.tracer_provider.force_flush()
                if flushed is False:
                    logger.warning("OpenTelemetry force flush timed out")
            except Exception:
                logger.exception("OpenTelemetry force flush failed")

    def shutdown(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self.tracer_provider is None:
                return
            try:
                self.tracer_provider.shutdown()
            except Exception:
                logger.exception("OpenTelemetry shutdown failed")


def initialize_telemetry() -> TelemetryProvider:
    """Install the configured SDK without making it a Runtime dependency."""

    if _env_truthy("OTEL_SDK_DISABLED"):
        logger.info("OpenTelemetry SDK disabled by environment")
        return TelemetryProvider(None)

    try:
        provider = _build_tracer_provider()
    except Exception:
        logger.exception("OpenTelemetry initialization failed")
        return TelemetryProvider(None)

    try:
        current = trace.get_tracer_provider()
        if not isinstance(current, TracerProvider):
            trace.set_tracer_provider(provider)
    except Exception:
        logger.exception("OpenTelemetry provider installation failed")
    return TelemetryProvider(provider)


def _build_tracer_provider() -> TracerProvider:
    provider = TracerProvider(resource=_resource())
    for processor in _span_processors(_exporter_names()):
        provider.add_span_processor(processor)
    return provider


def _resource() -> Resource:
    service_name = os.getenv("OTEL_SERVICE_NAME") or _DEFAULT_SERVICE_NAME
    return Resource.create({
        "service.name": service_name,
        "service.version": __version__,
    })


def _exporter_names() -> tuple[str, ...]:
    raw = os.getenv("OTEL_TRACES_EXPORTER", "none")
    names: list[str] = []
    for value in raw.split(","):
        name = value.strip().lower()
        if not name or name in names:
            continue
        names.append(name)
    if "none" in names:
        if len(names) > 1:
            logger.warning("OTEL_TRACES_EXPORTER=none disables other exporters")
        return ()
    unknown = [name for name in names if name not in _SUPPORTED_EXPORTERS]
    if unknown:
        logger.warning("Ignoring unsupported OpenTelemetry exporters: %s", unknown)
    return tuple(name for name in names if name in _SUPPORTED_EXPORTERS)


def _span_processors(names: Iterable[str]) -> tuple[SpanProcessor, ...]:
    processors: list[SpanProcessor] = []
    for name in names:
        try:
            if name == "console":
                from opentelemetry.sdk.trace.export import (
                    ConsoleSpanExporter,
                    SimpleSpanProcessor,
                )

                processors.append(
                    SimpleSpanProcessor(ConsoleSpanExporter(out=sys.stderr))
                )
            elif name == "otlp":
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter,
                )
                from opentelemetry.sdk.trace.export import BatchSpanProcessor

                endpoint = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
                exporter = OTLPSpanExporter(endpoint=endpoint or None)
                processors.append(BatchSpanProcessor(exporter))
        except Exception:
            logger.exception("OpenTelemetry %s exporter initialization failed", name)
    return tuple(processors)


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}
