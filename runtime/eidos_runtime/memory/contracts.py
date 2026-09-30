from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from eidos_runtime.models import EidosFrozenStrictModel, JsonSafeInt


MemoryKind = Literal["preference", "decision", "continuity", "experience", "event"]
MemoryStatus = Literal[
    "candidate", "active", "superseded", "archived", "quarantined", "forgotten"
]
EvidenceClass = Literal[
    "explicit_user", "observed_verified", "repeated_user", "inferred"
]
ScopeSelection = Literal["current", "global", "allowed"]
JobState = Literal[
    "queued",
    "running",
    "succeeded",
    "retry_wait",
    "paused_budget",
    "blocked_model",
    "superseded",
    "canceled",
    "failed",
]


class MemorySettings(EidosFrozenStrictModel):
    use_enabled: bool = True
    generate_enabled: bool = False
    daily_call_limit: int = Field(default=20, ge=0, le=1000)
    daily_token_limit: int = Field(default=100000, ge=0, le=10000000)
    debounce_seconds: int = Field(default=300, ge=0, le=3600)


class MemoryScope(EidosFrozenStrictModel):
    id: str
    kind: Literal["global", "project"]
    project_id: str | None = None
    settings: MemorySettings
    privacy_epoch: JsonSafeInt
    generation: JsonSafeInt


class MemoryEvidence(EidosFrozenStrictModel):
    item_id: str
    session_id: str
    run_id: str
    item_revision: JsonSafeInt = Field(ge=1)
    source_revision: JsonSafeInt = Field(ge=1)
    evidence_class: EvidenceClass


class MemoryEntry(EidosFrozenStrictModel):
    id: str
    scope_id: str
    revision: JsonSafeInt = Field(ge=1)
    kind: MemoryKind
    status: MemoryStatus
    title: str
    content: str
    aliases: list[str]
    evidence_class: EvidenceClass
    evidence: list[MemoryEvidence]
    pinned: bool
    user_owned: bool
    valid_from: JsonSafeInt | None = None
    valid_to: JsonSafeInt | None = None
    created_at: JsonSafeInt
    updated_at: JsonSafeInt
    last_used_at: JsonSafeInt | None = None
    use_count: JsonSafeInt = 0


class MemoryJob(EidosFrozenStrictModel):
    id: str
    scope_id: str
    session_id: str
    source_revision: int
    kind: Literal["extract", "consolidate"]
    state: JobState
    model_id: str
    attempts: int
    not_before: JsonSafeInt
    error_code: str | None = None
    tokens: JsonSafeInt
    estimated_usage: bool


class MemoryReadRequest(EidosFrozenStrictModel):
    session_id: str | None = None
    scope: ScopeSelection = "allowed"
    query: str = Field(default="", max_length=512)
    limit: int = Field(default=50, ge=1, le=100)
    cursor: int = Field(default=0, ge=0)
    include_history: bool = False
    valid_at: JsonSafeInt | None = None


class MemoryRebuildRequest(EidosFrozenStrictModel):
    session_id: str | None = None


class MemoryExportRequest(MemoryReadRequest):
    pass


class MemoryState(EidosFrozenStrictModel):
    scopes: list[MemoryScope]
    entries: list[MemoryEntry]
    jobs: list[MemoryJob]
    temporary: bool
    truncated: bool = False
    next_cursor: int | None = None
    trigram_available: bool


class MemorySettingsRequest(EidosFrozenStrictModel):
    session_id: str | None = None
    scope: Literal["current", "global"] = "global"
    settings: MemorySettings


class MemoryTemporaryRequest(EidosFrozenStrictModel):
    session_id: str
    temporary: bool


class MemoryRecord(EidosFrozenStrictModel):
    content: str = Field(min_length=1, max_length=8192)
    title: str = Field(default="", max_length=160)
    kind: MemoryKind = "preference"
    scope: Literal["current", "global"] = "current"
    aliases: list[str] = Field(default_factory=list, max_length=12)
    source_item_ids: list[str] = Field(default_factory=list, max_length=16)
    valid_from: JsonSafeInt | None = None
    valid_to: JsonSafeInt | None = None

    @model_validator(mode="after")
    def validate_content(self) -> MemoryRecord:
        if not self.content.strip() or any(
            not v.strip() or len(v) > 80 for v in self.aliases
        ):
            raise ValueError("memory_content_invalid")
        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_to <= self.valid_from
        ):
            raise ValueError("memory_time_invalid")
        return self


class MemoryWriteRequest(MemoryRecord):
    session_id: str | None = None
    operation_id: str = Field(min_length=1, max_length=256)


class MemoryManage(EidosFrozenStrictModel):
    action: Literal["correct", "forget", "pin", "unpin", "accept", "archive"]
    entry_id: str = Field(min_length=1, max_length=256)
    expected_revision: JsonSafeInt = Field(ge=1)
    content: str | None = Field(default=None, min_length=1, max_length=8192)

    @model_validator(mode="after")
    def validate_action(self) -> MemoryManage:
        if (self.action == "correct") != (self.content is not None):
            raise ValueError("memory_correction_content_required")
        if self.content is not None and not self.content.strip():
            raise ValueError("memory_content_invalid")
        return self


class MemoryManageRequest(MemoryManage):
    session_id: str | None = None
    operation_id: str = Field(min_length=1, max_length=256)


class MemoryActionResult(EidosFrozenStrictModel):
    operation_id: str
    status: Literal["applied", "pending", "rejected"]
    entry_id: str | None = None
    revision: int | None = None
    code: str


class MemoryGetRequest(EidosFrozenStrictModel):
    session_id: str | None = None
    entry_id: str
    revision: int | None = Field(default=None, ge=1)


class MemoryGetResult(EidosFrozenStrictModel):
    entry: MemoryEntry


class MemoryJobRequest(EidosFrozenStrictModel):
    session_id: str | None = None
    job_id: str


class MemoryBackfillRequest(EidosFrozenStrictModel):
    session_id: str
    operation_id: str


class MemoryExport(EidosFrozenStrictModel):
    markdown: str
    privacy_epochs: dict[str, int]
    encrypted: Literal[False] = False


class MemoryBackupRequest(EidosFrozenStrictModel):
    destination: str = Field(min_length=1, max_length=4096)


class MemoryBackupResult(EidosFrozenStrictModel):
    saved: Literal[True] = True
    encrypted: Literal[False] = False


class MemoryCandidate(MemoryRecord):
    evidence_class: EvidenceClass = "inferred"
    source_quotes: dict[str, str] = Field(default_factory=dict, max_length=16)


class MemoryExtraction(EidosFrozenStrictModel):
    candidates: list[MemoryCandidate] = Field(default_factory=list, max_length=16)
    summary: str = Field(default="", max_length=2048)


class MemoryProposal(EidosFrozenStrictModel):
    action: Literal["create", "corroborate", "revise", "supersede", "archive", "noop"]
    candidate_index: int = Field(ge=0, le=15)
    target_entry_id: str | None = None
    expected_revision: int | None = Field(default=None, ge=1)
    content: str | None = Field(default=None, min_length=1, max_length=8192)

    @model_validator(mode="after")
    def validate_target(self) -> MemoryProposal:
        if self.action in {"create", "noop"}:
            if self.target_entry_id is not None or self.expected_revision is not None:
                raise ValueError("memory_proposal_unexpected_target")
        elif self.target_entry_id is None or self.expected_revision is None:
            raise ValueError("memory_proposal_target_required")
        return self


class MemoryConsolidation(EidosFrozenStrictModel):
    changes: list[MemoryProposal] = Field(default_factory=list, max_length=16)


class MemoryProjectionRef(EidosFrozenStrictModel):
    entry_id: str
    revision: int


class MemoryProjection(EidosFrozenStrictModel):
    epochs: dict[str, int]
    generations: dict[str, int]
    entries: list[MemoryProjectionRef]
    rendered_payload: str
    token_estimate: int
