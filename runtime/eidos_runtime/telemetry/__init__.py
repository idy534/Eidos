"""OpenTelemetry integration for the Python Runtime."""

from eidos_runtime.telemetry.provider import (
    TelemetryProvider,
    initialize_telemetry,
)

__all__ = ["TelemetryProvider", "initialize_telemetry"]
