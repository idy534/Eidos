from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from eidos_runtime.models import EidosFrozenStrictModel, JsonSafeInt

MemoryMode = Literal["off", "read", "auto"]
MemoryKind = Literal["user", "feedback", "project", "reference"]
NonnegativeInt = Annotated[JsonSafeInt, Field(ge=0)]
PositiveRevision = Annotated[JsonSafeInt, Field(ge=1)]
ScopeSelection = Literal["current", "global", "allowed"]
MAX_CONTENT_CHARS = 65_536
GLOBAL_SCOPE_ID = "00000000-0000-4000-8000-000000000001"


def validate_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or len(value) > 200
        or path.is_absolute()
        or str(path) != value
        or "\\" in value
        or not value.endswith(".md")
        or any(part.startswith(".") for part in path.parts)
        or any(ord(char) < 32 for char in value)
    ):
        raise ValueError("memory_path_invalid")
    return value


class MemoryScope(EidosFrozenStrictModel):
    id: str
    kind: Literal["global", "project"]
    project_id: str | None = None
    mode: MemoryMode
    privacy_epoch: NonnegativeInt
    generation: NonnegativeInt


class MemoryEvidence(EidosFrozenStrictModel):
    item_id: str
    session_id: str
    run_id: str
    item_revision: PositiveRevision
    source_revision: PositiveRevision
    evidence_class: Literal["explicit_user", "observed_verified"]


class MemoryDocument(EidosFrozenStrictModel):
    id: str
    scope_id: str
    path: str
    revision: PositiveRevision
    title: str
    summary: str
    kind: MemoryKind
    user_owned: bool
    archived: bool = False
    created_at: JsonSafeInt
    updated_at: JsonSafeInt
    evidence: list[MemoryEvidence] = Field(default_factory=list)


class MemoryTarget(EidosFrozenStrictModel):
    session_id: str | None = None
    project_id: str | None = Field(default=None, min_length=1, max_length=256)


class MemoryListRequest(MemoryTarget):
    scope: ScopeSelection = "allowed"
    query: str = Field(default="", max_length=512)
    limit: int = Field(default=50, ge=1, le=100)
    cursor: int = Field(default=0, ge=0)
    include_archived: bool = False


class MemoryState(EidosFrozenStrictModel):
    scopes: list[MemoryScope]
    documents: list[MemoryDocument]
    next_cursor: int | None = None
    search_limited: bool = False


class MemoryReadRequest(MemoryTarget):
    scope: Literal["current", "global"] = "current"
    path: str = Field(default="MEMORY.md", max_length=200)
    revision: NonnegativeInt | None = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=8192, ge=1, le=16384)

    @model_validator(mode="after")
    def valid_path(self) -> Self:
        validate_path(self.path)
        return self


class MemoryReadResult(EidosFrozenStrictModel):
    path: str
    scope_id: str
    revision: NonnegativeInt
    current_revision: NonnegativeInt
    revisions: list[int] = Field(default_factory=list)
    document: MemoryDocument | None = None
    content: str
    offset: int
    next_offset: int | None = None
    epochs: dict[str, int] = Field(default_factory=dict)


class MemoryEdit(EidosFrozenStrictModel):
    scope: Literal["current", "global"] = "current"
    path: str = Field(min_length=1, max_length=200)
    action: Literal["write", "delete"] = "write"
    expected_revision: NonnegativeInt = 0
    title: str = Field(default="", max_length=160)
    summary: str = Field(default="", max_length=240)
    kind: MemoryKind = "feedback"
    content: str | None = Field(default=None, max_length=MAX_CONTENT_CHARS)
    source_item_ids: list[str] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def valid_edit(self) -> Self:
        validate_path(self.path)
        if self.path == "MEMORY.md":
            raise ValueError("memory_index_generated")
        if self.action == "write" and (
            not self.content or not self.content.strip()
        ):
            raise ValueError("memory_content_required")
        if self.action == "delete" and (
            self.content is not None or self.expected_revision == 0
        ):
            raise ValueError("memory_delete_revision_required")
        return self


class MemoryEditRequest(MemoryEdit):
    session_id: str | None = None
    operation_id: str = Field(min_length=1, max_length=256)
    project_id: str | None = Field(default=None, min_length=1, max_length=256)


class MemoryActionResult(EidosFrozenStrictModel):
    operation_id: str
    document_id: str
    path: str
    revision: JsonSafeInt
    code: Literal["memory_saved", "memory_unchanged", "memory_deleted", "memory_archived"]


class MemoryPolicyRequest(MemoryTarget):
    scope: Literal["current", "global"] = "global"
    mode: MemoryMode


class MemoryExportRequest(MemoryTarget):
    scope: Literal["current", "global"] = "current"
    cursor: int = Field(default=0, ge=0)


class MemoryExportFile(EidosFrozenStrictModel):
    path: str
    content: str


class MemoryExport(EidosFrozenStrictModel):
    files: list[MemoryExportFile]
    generation: JsonSafeInt
    next_cursor: int | None = None


class MemoryProjectionRef(EidosFrozenStrictModel):
    entry_id: str
    revision: int


class MemoryProjection(EidosFrozenStrictModel):
    epochs: dict[str, int]
    generations: dict[str, int]
    entries: list[MemoryProjectionRef]
    rendered_payload: str
    token_estimate: int


class MemoryChangedNotice(EidosFrozenStrictModel):
    scope_id: str
    generation: NonnegativeInt


class DataBackupRequest(EidosFrozenStrictModel):
    destination: str = Field(min_length=1, max_length=4096)


class DataBackupResult(EidosFrozenStrictModel):
    saved: Literal[True] = True
    encrypted: Literal[False] = False
