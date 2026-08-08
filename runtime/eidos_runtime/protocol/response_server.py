from __future__ import annotations

import json
import logging
import sys

from eidos_runtime.application.response_actions import ResponseActionApplication
from eidos_runtime.persistence.response_actions import ResponseActionRepository
from eidos_runtime.protocol.registry import MethodRegistration
from eidos_runtime.protocol.response_actions import (
    ItemSetFeedbackRequestDto,
    ItemSetFeedbackResponseDto,
    ResponseActionStateRequestDto,
    ResponseActionStateResponseDto,
    RunReviseRequestDto,
    RunReviseResponseDto,
)
from eidos_runtime.protocol.server import (
    RuntimeServer,
    _ApplicationMethodAdapter,
    _application_error_mapping,
    _model_from_environment,
    business_error,
    protocol_error,
    read_bounded_line,
    valid_request_id,
)


logger = logging.getLogger("eidos.runtime")


class ResponseActionRuntimeServer(RuntimeServer):
    """Runtime server with the response-action protocol slice registered."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        self._response_action_application: ResponseActionApplication | None = None
        super().__init__(*args, **kwargs)

    def _response_actions_or_error(self) -> ResponseActionApplication:
        application = self._response_action_application
        if application is not None:
            return application
        applications = self._applications_or_error()
        application = ResponseActionApplication(
            ResponseActionRepository(self.store),
            applications.runs,
        )
        self._response_action_application = application
        return application

    def _build_method_registry(self):  # type: ignore[no-untyped-def]
        registry = super()._build_method_registry()
        registrations = (
            (
                "responseAction/state",
                ResponseActionStateRequestDto,
                ResponseActionStateResponseDto,
                lambda _id, request: self._response_actions_or_error().state(request),
                True,
                True,
            ),
            (
                "item/setFeedback",
                ItemSetFeedbackRequestDto,
                ItemSetFeedbackResponseDto,
                lambda _id, request: self._response_actions_or_error().set_feedback(request),
                True,
                True,
            ),
            (
                "run/revise",
                RunReviseRequestDto,
                RunReviseResponseDto,
                lambda _id, request: self._response_actions_or_error().revise(request),
                False,
                False,
            ),
        )
        for (
            name,
            request_type,
            response_type,
            handler,
            allowed_when_draining,
            allowed_during_reconfiguration,
        ) in registrations:
            registry.register(
                MethodRegistration(
                    name=name,
                    request_type=request_type,
                    response_type=response_type,
                    handler=_ApplicationMethodAdapter(handler, response_type),
                    allowed_when_draining=allowed_when_draining,
                    allowed_during_reconfiguration=allowed_during_reconfiguration,
                    error_mapper=_application_error_mapping,
                )
            )
        return registry


def run() -> int:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    server = ResponseActionRuntimeServer(
        sys.stdout,
        model=_model_from_environment(),
    )

    try:
        while not server.shutting_down:
            raw_line, too_large = read_bounded_line(sys.stdin.buffer)
            if too_large:
                server.send(protocol_error(None, -32600, "Invalid Request"))
                continue
            if not raw_line:
                break

            try:
                message = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                server.send(protocol_error(None, -32700, "Parse error"))
                continue

            try:
                server.handle(message)
            except Exception:
                logger.exception("Runtime request failed")
                request_id = message.get("id") if isinstance(message, dict) else None
                if valid_request_id(request_id):
                    server.send(business_error(request_id, "INTERNAL_ERROR"))
    finally:
        server.close()

    return 0


__all__ = ["ResponseActionRuntimeServer", "run"]
