from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue

from eidos_runtime.models import EidosFrozenStrictModel, JsonSafeInt


class McpToolsRequest(EidosFrozenStrictModel):
    plugin_id: str = Field(min_length=1, max_length=128)
    server_id: str = Field(min_length=1, max_length=64)
    query: str = Field(default="", max_length=256)
    cursor: str | None = Field(default=None, pattern=r"^[a-f0-9-]{36}:[0-9]{1,3}$")


class McpToolDetail(EidosFrozenStrictModel):
    name: str = Field(min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=4096)
    input_schema: dict[str, JsonValue]
    available: bool = True


class McpToolsResponse(EidosFrozenStrictModel):
    state: Literal["ready", "not_authorized", "error"]
    description: str | None = Field(default=None, max_length=4096)
    tools: list[McpToolDetail] = Field(default_factory=list, max_length=10)
    total: JsonSafeInt = Field(default=0, ge=0, le=512)
    catalog_id: str | None = None
    next_cursor: str | None = None
    fetched_at: JsonSafeInt | None = None
    error_code: str | None = None
