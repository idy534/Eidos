from __future__ import annotations

from typing import Literal

from eidos_runtime.domain.automation import AutomationChanges, AutomationRequest, AutomationResponse, Identifier
from eidos_runtime.domain.approval_policy import ApprovalMode
from eidos_runtime.protocol.methods import MethodRequestDto, _OperationRequest
from pydantic.alias_generators import to_camel


class AutomationDesktopChanges(AutomationChanges):
    approval_mode: ApprovalMode | None = None


# The model tool must keep the narrower AutomationRequest.
class AutomationDesktopRequest(AutomationRequest):
    approval_mode: ApprovalMode | None = None
    changes: AutomationDesktopChanges | None = None


class AutomationExecuteRequest(_OperationRequest):
    model_config = {"alias_generator": to_camel}
    command: AutomationDesktopRequest
    context_session_id: Identifier | None = None
    full_access_confirmation: Literal["full-access-v1"] | None = None


class AutomationHistoryRequest(MethodRequestDto):
    id: Identifier
    cursor: str | None = None


class AutomationRunNowRequest(_OperationRequest):
    id: Identifier


class AutomationRefreshRequest(MethodRequestDto):
    pass


__all__ = ["AutomationExecuteRequest", "AutomationHistoryRequest", "AutomationRunNowRequest", "AutomationRefreshRequest", "AutomationResponse"]
