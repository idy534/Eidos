from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from eidos_runtime.models.base import EidosFrozenStrictModel
from eidos_runtime.domain.approval_policy import ApprovalMode, FULL_ACCESS_WARNING_VERSION

Identifier = Annotated[str, Field(min_length=36, max_length=36)]
ProjectIdentifier = Annotated[str, Field(min_length=1, max_length=128)]
Revision = Annotated[int, Field(ge=1, le=9_007_199_254_740_991)]


class AutomationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class Schedule(EidosFrozenStrictModel):
    expression: str | None = Field(default=None, max_length=128)
    run_at: str | None = Field(default=None, max_length=64)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_shape(self) -> Schedule:
        if (self.expression is None) == (self.run_at is None):
            raise ValueError("时间规则需要 expression 或 runAt，且只能提供一个。")
        return self


class AutomationModel(EidosFrozenStrictModel):
    model_id: str = Field(min_length=1, max_length=256)
    reasoning_selection: Literal["none", "thinking", "low", "medium", "high", "xhigh", "max"] | None = None


class AutomationExecution(EidosFrozenStrictModel):
    execution_mode: Literal["local", "worktree"] = "local"
    base_ref: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_ref(self) -> AutomationExecution:
        if self.base_ref is not None and self.execution_mode != "worktree":
            raise ValueError("起始分支只用于工作树。")
        return self


class AutomationTarget(EidosFrozenStrictModel):
    session_id: Identifier | None = None
    project_id: ProjectIdentifier | None = None
    execution: AutomationExecution | None = None

    @field_validator("session_id")
    @classmethod
    def validate_id(cls, value: str | None) -> str | None:
        if value is not None and str(UUID(value)) != value:
            raise ValueError("ID 必须使用规范 UUID。")
        return value

    @model_validator(mode="after")
    def validate_target(self) -> AutomationTarget:
        if "session_id" in self.model_fields_set and self.session_id is None:
            raise ValueError("sessionId 不能为 null。")
        if self.session_id is not None and self.execution is not None:
            raise ValueError("已有会话沿用自己的执行环境。")
        if "execution" in self.model_fields_set and self.execution is None:
            raise ValueError("execution 不能为 null。")
        if self.session_id is None and "project_id" not in self.model_fields_set:
            raise ValueError("切换为新会话时需要明确 projectId。")
        return self


class AutomationChanges(EidosFrozenStrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    prompt: str | None = Field(default=None, min_length=1, max_length=16384)
    kind: Literal["at", "cron"] | None = None
    schedule: Schedule | None = None
    status: Literal["ACTIVE", "PAUSED"] | None = None
    model: AutomationModel | None = None
    approval_mode: Literal["manual", "auto_review"] | None = None
    target: AutomationTarget | None = None

    @model_validator(mode="after")
    def validate_changes(self) -> AutomationChanges:
        if not self.model_fields_set or any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("changes 不能为空，修改值不能为 null。")
        if self.kind is not None and self.schedule is None:
            raise ValueError("切换时间类型时必须提供 schedule。")
        return self


class AutomationRequest(EidosFrozenStrictModel):
    """One model-visible object; strict per-mode combinations remain Runtime-owned."""

    mode: Literal["view", "create", "update", "delete", "preview"]
    id: Identifier | None = None
    expected_revision: Revision | None = None
    changes: AutomationChanges | None = None
    kind: Literal["at", "cron"] | None = None
    name: str | None = Field(default=None, min_length=1, max_length=80)
    prompt: str | None = Field(default=None, min_length=1, max_length=16384)
    schedule: Schedule | None = None
    status: Literal["ACTIVE", "PAUSED"] | None = None
    model: AutomationModel | None = None
    approval_mode: Literal["manual", "auto_review"] | None = None
    session_id: Identifier | None = None
    project_id: ProjectIdentifier | None = None
    execution: AutomationExecution | None = None
    scope: Literal["current_session", "current_project", "all"] | None = None
    query: str | None = Field(default=None, max_length=256)
    cursor: str | None = Field(default=None, max_length=2048)
    limit: int | None = Field(default=None, ge=1, le=20)

    _validate_ids = field_validator("id", "session_id")(AutomationTarget.validate_id.__func__)

    def receipt_request(self) -> dict[str, object]:
        # Explicit projectId=null is meaningful at both the root and target.
        return self.model_dump(mode="json", by_alias=True, exclude_unset=True)

    @model_validator(mode="after")
    def validate_mode(self) -> AutomationRequest:
        present = self.model_fields_set - {"mode"}
        allowed = {
            "view": ({"id"} if self.id else {"scope", "query", "cursor", "limit", "status"}),
            "create": {"kind", "name", "prompt", "schedule", "status", "model", "approval_mode", "session_id", "project_id", "execution"},
            "update": {"id", "expected_revision", "changes"},
            "delete": {"id", "expected_revision"},
            "preview": {"kind", "schedule"},
        }[self.mode]
        required = {
            "view": set(), "create": {"kind", "name", "prompt", "schedule"},
            "update": {"id", "expected_revision", "changes"},
            "delete": {"id", "expected_revision"}, "preview": {"kind", "schedule"},
        }[self.mode]
        if not required <= present or not present <= allowed:
            raise ValueError("参数与 mode 不匹配。")
        if any(getattr(self, key) is None for key in present - {"project_id"}):
            raise ValueError("参数不能为 null；无项目使用 projectId=null。")
        if self.session_id and self.execution:
            raise ValueError("已有会话不能指定 execution。")
        for text in (self.name, self.prompt, self.changes.name if self.changes else None, self.changes.prompt if self.changes else None):
            if text is not None and (not text.strip() or len(text.encode()) > 16384):
                raise ValueError("名称或任务正文为空或过长。")
        for schedule in (self.schedule, self.changes.schedule if self.changes else None):
            if schedule is not None and any(getattr(schedule, key) is None for key in schedule.model_fields_set):
                raise ValueError("时间字段不能为 null。")
        for model in (self.model, self.changes.model if self.changes else None):
            if model is not None and "reasoning_selection" in model.model_fields_set and model.reasoning_selection is None:
                raise ValueError("reasoningSelection 不能为 null。")
        return self


class AutomationBinding(EidosFrozenStrictModel):
    kind: Literal["existing_session", "new_session"]
    session_id: Identifier | None = None
    project_id: ProjectIdentifier | None = None
    execution: AutomationExecution = Field(default_factory=AutomationExecution)


class Automation(EidosFrozenStrictModel):
    id: Identifier
    revision: Revision
    name: str
    prompt: str
    kind: Literal["at", "cron"]
    schedule: Schedule
    status: Literal["ACTIVE", "PAUSED"] = "ACTIVE"
    model: AutomationModel
    approval_mode: ApprovalMode = "auto_review"
    full_access_confirmation: Literal["full-access-v1"] | None = Field(default=None, exclude_if=lambda value: value is None)
    target: AutomationBinding
    created_from_session_id: str | None = None
    deleted_at: int | None = None
    pause_reason: str | None = None
    created_at: int
    updated_at: int

    @model_validator(mode="after")
    def validate_full_access(self) -> Automation:
        if (self.approval_mode == "full_access") != (self.full_access_confirmation == FULL_ACCESS_WARNING_VERSION):
            raise ValueError("完全访问计划需要保存用户确认。")
        return self

    def session_title(self) -> str:
        title = self.name[:60]
        while len(title.encode("utf-8")) > 120:
            title = title[:-1]
        return title


class AutomationOccurrence(EidosFrozenStrictModel):
    id: str
    automation_id: str
    revision: int
    scheduled_for: str
    status: Literal["preparing", "submitted", "skipped", "failed", "interrupted"]
    snapshot: Automation
    run_id: str | None = None
    session_id: str | None = None
    reason: str | None = None
    created_at: int
    run_status: str | None = None
    reconciliation_required: bool = False


class AutomationOrigin(EidosFrozenStrictModel):
    automation_id: str
    revision: int
    occurrence_id: str
    scheduled_for: str


class AutomationSummary(EidosFrozenStrictModel):
    id: str
    revision: int
    name: str
    status: Literal["ACTIVE", "PAUSED"]
    kind: Literal["at", "cron"]
    schedule: Schedule
    target: AutomationBinding
    model: AutomationModel
    approval_mode: ApprovalMode
    next_run_at: str | None = None
    pause_reason: str | None = None


class AutomationResponse(EidosFrozenStrictModel):
    kind: Literal["detail", "list", "preview", "mutation", "history", "run"]
    automation: Automation | None = None
    items: list[AutomationSummary] = Field(default_factory=list)
    occurrences: list[AutomationOccurrence] = Field(default_factory=list)
    next_cursor: str | None = None
    next_run_at: str | None = None
    next_runs: list[str] = Field(default_factory=list)
    schedule: Schedule | None = None
    changed: bool = False
    replayed: bool = False
    occurrence_id: str | None = None
