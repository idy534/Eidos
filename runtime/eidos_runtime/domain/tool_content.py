from __future__ import annotations

import base64
import binascii
from typing import Annotated, Literal

from pydantic import Field, StrictStr, field_validator, model_validator

from eidos_runtime.models import EidosFrozenStrictModel


MAX_TOOL_CONTENT_BYTES = 10 * 1024 * 1024
MAX_CONTEXT_CONTENT_BYTES = 16 * 1024 * 1024


class ToolTextContent(EidosFrozenStrictModel):
    kind: Literal["text"] = "text"
    text: StrictStr = Field(max_length=MAX_TOOL_CONTENT_BYTES)


class ToolBinaryContent(EidosFrozenStrictModel):
    kind: Literal["binary"] = "binary"
    mime: StrictStr = Field(max_length=128, pattern=r"^[a-z0-9.+-]+/[a-z0-9.+-]+$")
    data: StrictStr = Field(max_length=4 * ((MAX_TOOL_CONTENT_BYTES + 2) // 3))

    @field_validator("data")
    @classmethod
    def validate_data(cls, value: str) -> str:
        try:
            size = len(base64.b64decode(value, validate=True))
        except (ValueError, binascii.Error):
            raise ValueError("invalid_tool_content_encoding") from None
        if not 0 < size <= MAX_TOOL_CONTENT_BYTES:
            raise ValueError("tool_content_too_large")
        return value

    @classmethod
    def from_bytes(cls, data: bytes, mime: str) -> ToolBinaryContent:
        if not 0 < len(data) <= MAX_TOOL_CONTENT_BYTES:
            raise ValueError("tool_content_too_large")
        return cls(mime=mime, data=base64.b64encode(data).decode("ascii"))


ToolContentItem = Annotated[ToolTextContent | ToolBinaryContent, Field(discriminator="kind")]


class ToolContentSnapshot(EidosFrozenStrictModel):
    schema_version: Literal[1] = 1
    items: tuple[ToolContentItem, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_total_size(self) -> ToolContentSnapshot:
        size = sum(
            len(item.text.encode("utf-8")) if isinstance(item, ToolTextContent)
            else len(base64.b64decode(item.data))
            for item in self.items
        )
        if size > MAX_TOOL_CONTENT_BYTES:
            raise ValueError("tool_content_too_large")
        return self
