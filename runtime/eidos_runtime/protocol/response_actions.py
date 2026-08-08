from __future__ import annotations

import uuid
from typing import ClassVar, Literal

from pydantic import Field, StrictStr, model_validator

from eidos_runtime.protocol.methods import MethodRequestDto, MethodResultDto
from eidos_runtime.protocol.schemas import ClosedModel, RunDto


class _CanonicalRequest(MethodRequestDto):
    _canonical_id_fields: ClassVar[tuple[str, ...]] = ()

    @model_validator(mode="after")
    def _validate_canonical_ids(self) -> "_CanonicalRequest":
        for field_name in self._canonical_id_fields:
            value = getattr(self, field_name)
            if value is None:
                continue
            try:
                if str(uuid.UUID(value)) != value:
                    raise ValueError
            except ValueError as error:
                raise ValueError(f"{field_name} must be a canonical UUID") from error
        return self


class ResponseActionStateRequestDto(_CanonicalRequest):
    session_id: StrictStr = Field(alias="sessionId")
    _canonical_id_fields: ClassVar[tuple[str, ...]] = ("session_id",)


class ItemSetFeedbackRequestDto(_CanonicalRequest):
    item_id: StrictStr = Field(alias="itemId")
    feedback: Literal["up", "down"] | None = None
    _canonical_id_fields: ClassVar[tuple[str, ...]] = ("item_id",)


class RunReviseRequestDto(_CanonicalRequest):
    source_run_id: StrictStr = Field(alias="sourceRunId")
    user_input: StrictStr | None = Field(
        default=None,
        alias="userInput",
        min_length=1,
        max_length=64 * 1024,
    )
    operation_id: StrictStr | None = Field(default=None, alias="operationId")
    _canonical_id_fields: ClassVar[tuple[str, ...]] = (
        "source_run_id",
        "operation_id",
    )

    @model_validator(mode="after")
    def _validate_revision_input(self) -> "RunReviseRequestDto":
        if self.user_input is not None and not self.user_input.strip():
            raise ValueError("userInput must not be blank")
        return self


class ResponseFeedbackStateDto(ClosedModel):
    item_id: StrictStr = Field(alias="itemId")
    value: Literal["up", "down"]


class RunRevisionStateDto(ClosedModel):
    run_id: StrictStr = Field(alias="runId")
    source_run_id: StrictStr = Field(alias="sourceRunId")
    kind: Literal["regenerate", "edit"]


class ResponseActionStateResponseDto(MethodResultDto):
    feedback: list[ResponseFeedbackStateDto]
    revisions: list[RunRevisionStateDto]


class ItemSetFeedbackResponseDto(MethodResultDto):
    item_id: StrictStr = Field(alias="itemId")
    feedback: Literal["up", "down"] | None = None


class RunReviseResponseDto(MethodResultDto):
    run: RunDto
    source_run_id: StrictStr = Field(alias="sourceRunId")
    kind: Literal["regenerate", "edit"]


__all__ = [
    "ItemSetFeedbackRequestDto",
    "ItemSetFeedbackResponseDto",
    "ResponseActionStateRequestDto",
    "ResponseActionStateResponseDto",
    "RunReviseRequestDto",
    "RunReviseResponseDto",
]
