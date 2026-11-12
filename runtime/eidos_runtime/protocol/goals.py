from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field, model_validator
from pydantic.alias_generators import to_camel

from eidos_runtime.domain.goal import GoalRevision, GoalView
from eidos_runtime.domain.automation import AutomationModel
from eidos_runtime.domain.approval_policy import ApprovalMode
from eidos_runtime.protocol.methods import _OperationRequest
from eidos_runtime.models.base import EidosFrozenStrictModel


class GoalRequest(_OperationRequest):
    model_config = {"alias_generator": to_camel}
    _canonical_id_fields: ClassVar[tuple[str, ...]] = ("operation_id", "session_id")
    action: Literal["get", "pause", "resume", "clear"]
    session_id: str = Field(min_length=36, max_length=36)
    expected_revision: GoalRevision | None = None
    model: AutomationModel | None = None
    approval_mode: ApprovalMode | None = None
    full_access_confirmation: Literal["full-access-v1"] | None = None

    @model_validator(mode="after")
    def validate_command(self) -> GoalRequest:
        if self.action != "get" and self.expected_revision is None:
            raise ValueError("goal_revision_required")
        configured = self.model is not None or self.approval_mode is not None or self.full_access_confirmation is not None
        if configured and (self.action != "resume" or self.model is None or self.approval_mode is None):
            raise ValueError("only_resume_accepts_a_model_and_access_mode")
        if self.approval_mode == "full_access" and self.full_access_confirmation != "full-access-v1":
            raise ValueError("full access requires explicit confirmation")
        if self.approval_mode not in {None, "full_access"} and self.full_access_confirmation is not None:
            raise ValueError("full access confirmation does not match approval mode")
        return self


class GoalGetRequest(GoalRequest):
    """`goal/get` only reads."""
    action: Literal["get"]


class GoalUpdateRequest(GoalRequest):
    """`goal/update` pauses or resumes."""
    action: Literal["pause", "resume"]


class GoalClearRequest(GoalRequest):
    """`goal/clear` only clears."""
    action: Literal["clear"]


class GoalChangedNotice(EidosFrozenStrictModel):
    session_id: str = Field(min_length=36, max_length=36)


__all__ = [
    "GoalRequest", "GoalGetRequest", "GoalUpdateRequest", "GoalClearRequest",
    "GoalView", "GoalChangedNotice",
]
