from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field, JsonValue, StrictInt, StrictStr, field_validator, model_validator

from eidos_runtime.protocol.methods import (
    MethodResultDto,
    _CanonicalIdRequest,
    _OperationRequest,
    _git_relative_path,
)
from eidos_runtime.protocol.schemas import ClosedModel


class ReviewCommentDto(ClosedModel):
    id: StrictStr
    session_id: StrictStr = Field(alias="sessionId")
    path: StrictStr
    scope: Literal["head", "baseline"]
    side: Literal["old", "new"]
    line: StrictInt = Field(ge=1)
    body: StrictStr
    base_head: StrictStr = Field(alias="baseHead")
    diff_hash: StrictStr = Field(alias="diffHash")
    status: Literal["active", "stale"]
    created_at: StrictInt = Field(alias="createdAt", ge=0)
    updated_at: StrictInt = Field(alias="updatedAt", ge=0)


class ReviewCommentListRequestDto(_CanonicalIdRequest):
    session_id: StrictStr = Field(alias="sessionId")
    path: StrictStr | None = Field(default=None, min_length=1, max_length=4096)
    scope: Literal["head", "baseline"] | None = None
    _canonical_id_fields: ClassVar[tuple[str, ...]] = ("session_id",)

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str | None) -> str | None:
        return None if value is None else _git_relative_path(value)

    @model_validator(mode="after")
    def _validate_filter(self) -> "ReviewCommentListRequestDto":
        if (self.path is None) != (self.scope is None):
            raise ValueError("review path and scope must be provided together")
        return self


class ReviewCommentListResponseDto(MethodResultDto):
    comments: list[ReviewCommentDto]


class ReviewCommentCreateRequestDto(_OperationRequest):
    operation_id: StrictStr = Field(alias="operationId")
    comment_id: StrictStr = Field(alias="commentId")
    session_id: StrictStr = Field(alias="sessionId")
    path: StrictStr = Field(min_length=1, max_length=4096)
    scope: Literal["head", "baseline"]
    side: Literal["old", "new"]
    line: StrictInt = Field(ge=1)
    body: StrictStr = Field(min_length=1, max_length=16_384)
    base_head: StrictStr = Field(alias="baseHead", min_length=40, max_length=64)
    diff_hash: StrictStr = Field(alias="diffHash", min_length=64, max_length=64)
    _canonical_id_fields: ClassVar[tuple[str, ...]] = (
        "operation_id", "comment_id", "session_id",
    )

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        return _git_relative_path(value)

    @field_validator("body")
    @classmethod
    def _validate_body(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("review body is invalid")
        return value

    @field_validator("base_head", "diff_hash")
    @classmethod
    def _validate_hash(cls, value: str) -> str:
        if any(character not in "0123456789abcdef" for character in value):
            raise ValueError("review hash is invalid")
        return value


class ReviewCommentCreateResponseDto(MethodResultDto):
    comment: ReviewCommentDto


class ReviewCommentDeleteRequestDto(_OperationRequest):
    operation_id: StrictStr = Field(alias="operationId")
    session_id: StrictStr = Field(alias="sessionId")
    comment_id: StrictStr = Field(alias="commentId")
    _canonical_id_fields: ClassVar[tuple[str, ...]] = (
        "operation_id", "session_id", "comment_id",
    )


class ReviewCommentDeleteResponseDto(MethodResultDto):
    comment_id: StrictStr = Field(alias="commentId")

    def to_json_value(self) -> dict[str, JsonValue]:
        return self.model_dump(mode="json", by_alias=True)


__all__ = [
    "ReviewCommentCreateRequestDto",
    "ReviewCommentCreateResponseDto",
    "ReviewCommentDeleteRequestDto",
    "ReviewCommentDeleteResponseDto",
    "ReviewCommentDto",
    "ReviewCommentListRequestDto",
    "ReviewCommentListResponseDto",
]
