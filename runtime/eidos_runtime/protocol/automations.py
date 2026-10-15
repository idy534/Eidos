from __future__ import annotations

from eidos_runtime.domain.automation import AutomationRequest, AutomationResponse, Identifier
from eidos_runtime.protocol.methods import MethodRequestDto, _OperationRequest
from pydantic.alias_generators import to_camel


class AutomationExecuteRequest(_OperationRequest):
    model_config = {"alias_generator": to_camel}
    command: AutomationRequest
    context_session_id: Identifier | None = None


class AutomationHistoryRequest(MethodRequestDto):
    id: Identifier
    cursor: str | None = None


class AutomationRunNowRequest(_OperationRequest):
    id: Identifier


class AutomationRefreshRequest(MethodRequestDto):
    pass


__all__ = ["AutomationExecuteRequest", "AutomationHistoryRequest", "AutomationRunNowRequest", "AutomationRefreshRequest", "AutomationResponse"]
