from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import json
import threading
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from eidos_runtime.db.database import now_ms
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.db.errors import OperationConflictError, OperationInProgressError
from eidos_runtime.domain.automation import (
    Automation, AutomationBinding, AutomationError, AutomationExecution, AutomationModel,
    AutomationRequest, AutomationResponse, AutomationSummary, AutomationTarget, Schedule,
)
from eidos_runtime.infrastructure.automation_time import future_times, normalize
from eidos_runtime.persistence.automations import AutomationRepository
from eidos_runtime.domain.approval_policy import FULL_ACCESS_WARNING_VERSION


class AutomationApplication:
    def __init__(
        self, store: SessionStore, *, resolve_model: Callable[[AutomationModel], AutomationModel],
        publish: Callable[[], object], launch: Callable[[str], None],
        scan_text: Callable[[str], str] = lambda text: text,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.store = store
        self.repository = AutomationRepository(store.database)
        self.resolve_model = resolve_model
        self.publish = publish
        self.launch = launch
        self.scan_text = scan_text
        self.clock = clock

    def execute(
        self, request: AutomationRequest, *, context_session_id: str | None,
        operation_id: str | None = None, run_id: str | None = None,
        cancel: threading.Event | None = None,
        prepare_only: bool = False,
        full_access_confirmation: str | None = None,
    ) -> AutomationResponse:
        origin = self.repository.origin(run_id) if run_id else None
        if run_id:
            from eidos_runtime.persistence.collaboration import CollaborationRepository
            if CollaborationRepository(self.store.database).is_child(str(self.store.read_run(run_id)["sessionId"])):
                raise AutomationError("automation_source_not_allowed", "子任务不能管理定时任务。")
        if origin is not None and not (
            request.mode == "update" and request.id == origin.automation_id
            and request.expected_revision == origin.revision and request.changes is not None
            and request.changes.model_fields_set == {"status"} and request.changes.status == "PAUSED"
        ):
            raise AutomationError("automation_source_not_allowed", "自动任务只能暂停自身计划。")
        if request.mode == "view":
            if request.id:
                return self.detail(self.repository.read(request.id))
            return self._list(request, context_session_id)
        if request.mode == "preview":
            assert request.kind and request.schedule
            schedule = normalize(request.kind, request.schedule)
            times = future_times(request.kind, schedule, self.clock())
            return AutomationResponse(kind="preview", schedule=schedule, next_runs=times, next_run_at=times[0] if times else None)
        if operation_id is None:
            raise AutomationError("automation_operation_conflict", "写入操作缺少请求标识。")
        wire = request.receipt_request()
        if full_access_confirmation is not None:
            wire["fullAccessConfirmation"] = full_access_confirmation
        if run_id and request.mode == "create":
            operation_id = str(uuid5(NAMESPACE_URL, "automation/create/" + self.repository.logical_request(run_id) + "/" + json.dumps(wire, sort_keys=True, ensure_ascii=False)))
        try:
            replay = self.repository.replay(operation_id, wire)
        except (OperationConflictError, OperationInProgressError):
            raise AutomationError("automation_operation_conflict", "请求标识已使用或请求仍在处理中。") from None
        if replay is not None:
            return self.detail(replay).model_copy(update={"kind": "mutation", "replayed": True})
        if request.mode == "create":
            assert request.name and request.prompt and request.kind and request.schedule
            if request.approval_mode == "full_access":
                self._require_full_access_confirmation(run_id, full_access_confirmation)
            default_model: AutomationModel | None = None
            if run_id:
                profile = self.store.read_model_profile(run_id)
                default_model = AutomationModel(model_id=profile.model_id, reasoning_selection=profile.reasoning_selection)
            if request.model is None and default_model is None:
                raise AutomationError("automation_model_unavailable", "请选择执行任务使用的模型。")
            selected = request.model or default_model
            assert selected is not None
            model = self.resolve_model(selected)
            binding = self._binding(request.session_id, request.project_id, "project_id" in request.model_fields_set, request.execution, context_session_id)
            schedule = normalize(request.kind, request.schedule)
            self._validate_future(request.kind, schedule)
            now = now_ms()
            task = Automation(id=str(uuid4()), revision=1, name=request.name.strip(), prompt=self.scan_text(request.prompt),
                kind=request.kind, schedule=schedule, model=model, target=binding,
                status=request.status or "ACTIVE", approval_mode=request.approval_mode or "auto_review",
                full_access_confirmation=FULL_ACCESS_WARNING_VERSION if request.approval_mode == "full_access" else None,
                created_from_session_id=context_session_id, created_at=now, updated_at=now)
            expected = None
        else:
            assert request.id and request.expected_revision
            current = self.repository.read(request.id)
            if run_id and current.approval_mode == "full_access" and request.mode == "update":
                changes = request.changes
                assert changes is not None
                safe_pause = changes.model_fields_set == {"status"} and changes.status == "PAUSED"
                downgrade = changes.approval_mode in {"manual", "auto_review"}
                if not safe_pause and not downgrade:
                    raise AutomationError("automation_source_not_allowed", "完全访问任务请在定时任务页面手动编辑；对话中可以暂停、删除或降低审批权限。")
            if current.deleted_at is not None and request.mode == "update":
                raise AutomationError("automation_not_found", "这个定时任务已删除，不能继续修改。")
            if current.revision != request.expected_revision:
                raise AutomationError("automation_revision_conflict", "任务已被修改，请重新查看后再更新。")
            # A repeated ToolCall must reach the receipt before revision validation.
            task = current
            expected = request.expected_revision
            if request.mode == "delete":
                task = current.model_copy(update={"deleted_at": now_ms(), "status": "PAUSED"})
            else:
                assert request.changes
                changes = request.changes
                if changes.approval_mode == "full_access":
                    self._require_full_access_confirmation(run_id, full_access_confirmation)
                updates = changes.model_dump(exclude_unset=True, exclude={"target", "model", "schedule"})
                if changes.approval_mode is not None:
                    updates["full_access_confirmation"] = FULL_ACCESS_WARNING_VERSION if changes.approval_mode == "full_access" else None
                if changes.prompt is not None:
                    updates["prompt"] = self.scan_text(changes.prompt)
                if changes.target is not None:
                    target: AutomationTarget = changes.target
                    updates["target"] = self._binding(target.session_id, target.project_id, "project_id" in target.model_fields_set, target.execution, None)
                if changes.model is not None:
                    updates["model"] = self.resolve_model(changes.model)
                if changes.schedule is not None:
                    updates["schedule"] = normalize(changes.kind or current.kind, changes.schedule, default_timezone=current.schedule.timezone)
                candidate = current.model_copy(update=updates)
                if candidate.status == "ACTIVE" and (changes.schedule is not None or changes.status == "ACTIVE"):
                    self._validate_future(candidate.kind, candidate.schedule)
                if changes.status == "ACTIVE":
                    candidate = candidate.model_copy(update={"pause_reason": None})
                task = candidate
            if task != current:
                task = task.model_copy(update={"revision": current.revision + 1, "updated_at": now_ms()})
        if prepare_only:
            return AutomationResponse(kind="detail", automation=task)
        try:
            mutation = self.repository.save(task, expected=expected, operation_id=operation_id,
                request=wire, cancel=cancel.is_set if cancel else lambda: False)
        except (OperationConflictError, OperationInProgressError):
            raise AutomationError("automation_operation_conflict", "请求标识已使用或请求仍在处理中。") from None
        self.publish()
        response = self.detail(mutation.value)
        return response.model_copy(update={"kind": "mutation", "changed": bool(mutation.events), "replayed": not mutation.events and mutation.value != task})

    def _validate_future(self, kind: str, schedule: Schedule) -> None:
        if not future_times(kind, schedule, self.clock(), 1):
            raise AutomationError("automation_time_in_past", "执行时间已过去，请设置未来时间。")

    @staticmethod
    def _require_full_access_confirmation(run_id: str | None, confirmation: str | None) -> None:
        if run_id is not None or confirmation != FULL_ACCESS_WARNING_VERSION:
            raise AutomationError("automation_confirmation_required", "开启完全访问需要在定时任务页面由用户确认。")

    def _binding(self, session_id: str | None, project_id: str | None, explicit_project: bool,
                 execution: AutomationExecution | None, context_session_id: str | None) -> AutomationBinding:
        if session_id is not None:
            binding = self.repository.binding(session_id)
            if explicit_project and project_id != binding.project_id:
                raise AutomationError("automation_target_unavailable", "项目与目标会话不一致。")
            return binding
        if not explicit_project and context_session_id:
            project_id = self.repository.binding(context_session_id).project_id
        if project_id is not None:
            self.repository.project_root(project_id)
            if execution:
                self.repository.validate_execution(project_id, execution)
        if execution and execution.execution_mode == "worktree" and project_id is None:
            raise AutomationError("automation_target_unavailable", "无项目任务不能使用工作树。")
        return AutomationBinding(kind="new_session", project_id=project_id, execution=execution or AutomationExecution())

    def detail(self, task: Automation) -> AutomationResponse:
        times = future_times(task.kind, task.schedule, self.clock()) if task.status == "ACTIVE" and task.deleted_at is None else []
        return AutomationResponse(kind="detail", automation=task, next_runs=times, next_run_at=times[0] if times else None)

    def _list(self, request: AutomationRequest, session_id: str | None) -> AutomationResponse:
        scope = request.scope or "current_session"
        project = self.repository.binding(session_id).project_id if session_id else None
        try:
            after = int(request.cursor or "0")
            if after < 0:
                raise ValueError
        except ValueError:
            raise AutomationError("invalid_arguments", "分页游标无效。") from None
        items: list[AutomationSummary] = []
        rows = self.repository.list(after, 200)
        last = after
        for seq, task in rows:
            last = seq
            if task.deleted_at is not None or (request.status and task.status != request.status):
                continue
            if scope == "current_session" and task.target.session_id != session_id and task.created_from_session_id != session_id:
                continue
            if scope == "current_project" and task.target.project_id != project:
                continue
            if request.query and request.query.casefold() not in task.name.casefold():
                continue
            values = task.model_dump(exclude={"prompt", "created_from_session_id", "deleted_at", "created_at", "updated_at", "full_access_confirmation"})
            values["next_run_at"] = self.detail(task).next_run_at
            items.append(AutomationSummary.model_validate(values))
            if len(items) >= (request.limit or 10):
                break
        next_cursor = str(last) if rows and (last < rows[-1][0] or len(rows) == 200) else None
        return AutomationResponse(kind="list", items=items, next_cursor=next_cursor)

    def run_now(self, task_id: str, operation_id: str) -> AutomationResponse:
        UUID(operation_id)
        occurrence = self.repository.claim(task_id, "manual:" + operation_id)
        if occurrence and occurrence.status == "preparing":
            self.launch(occurrence.id)
        self.publish()
        return AutomationResponse(kind="run", occurrence_id=occurrence.id if occurrence else None)
