from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator
from pydantic.alias_generators import to_camel

from eidos_runtime.protocol.methods import MethodRequestDto, MethodResultDto
from eidos_runtime.protocol.schemas import ClosedModel


class SearchRequest(MethodRequestDto):
    model_config = {
        "alias_generator": to_camel,
        "json_schema_extra": {
            "not": {
                "required": ["projectId", "projectless"],
                "properties": {
                    "projectless": {"const": True},
                    "projectId": {"type": "string"},
                },
            },
        },
    }
    query: str = Field(min_length=1, max_length=256, pattern=r"\S")
    session_id: str | None = Field(default=None, min_length=1, max_length=256)
    project_id: str | None = Field(default=None, min_length=1, max_length=256)
    projectless: bool = False
    cursor: str | None = Field(default=None, max_length=1024)
    limit: int = Field(default=30, ge=1, le=100)

    @model_validator(mode="after")
    def validate_scope(self) -> SearchRequest:
        if not self.query.strip() or self.projectless and self.project_id:
            raise ValueError("invalid search scope or query")
        return self


class SearchHit(ClosedModel):
    model_config = {"alias_generator": to_camel}
    session_id: str
    title: str
    project_name: str
    item_id: str | None = None
    snippet: str = Field(max_length=300)
    occurrence: int = Field(default=0, ge=0)
    match_kind: Literal["title", "message"]


class SearchProjectHit(ClosedModel):
    id: str
    name: str


class SearchResponse(MethodResultDto):
    model_config = {"alias_generator": to_camel}
    items: list[SearchHit] = Field(max_length=100)
    next_cursor: str | None = None
    indexing: bool = False
    projects: list[SearchProjectHit] = Field(default_factory=list, max_length=10)
    reset: bool = False


class ReadAroundRequest(MethodRequestDto):
    model_config = {"alias_generator": to_camel}
    session_id: str = Field(min_length=1, max_length=256)
    item_id: str = Field(min_length=1, max_length=256)


class FileFindRequest(MethodRequestDto):
    model_config = {"alias_generator": to_camel}
    session_id: str = Field(min_length=1, max_length=256)
    path: str = Field(min_length=1, max_length=4096)
    version: str = Field(min_length=64, max_length=64)
    offset: int = Field(default=0, ge=0, le=2 * 1024 * 1024)


class FileFindResponse(MethodResultDto):
    model_config = {"alias_generator": to_camel}
    path: str
    kind: Literal["text", "code", "markdown", "html"]
    language: str | None = None
    version: str = Field(min_length=64, max_length=64)
    data: str = Field(max_length=256 * 1024)
    size_bytes: int = Field(ge=0)
    next_offset: int = Field(ge=0, le=2 * 1024 * 1024)
    complete: bool
