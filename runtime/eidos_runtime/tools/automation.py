from __future__ import annotations

from dataclasses import dataclass
import hashlib
import threading
from typing import TYPE_CHECKING, ClassVar
from uuid import NAMESPACE_URL, uuid5

from pydantic import ValidationError

from eidos_runtime.domain.automation import AutomationError, AutomationRequest, AutomationResponse
from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.runtime.errors import tool_result
from eidos_runtime.tools.contracts import StrictToolModel, result_model
from eidos_runtime.tools.registry import AdapterToolRuntime, ToolProvenance, ToolRegistryEntry, ToolSpec

if TYPE_CHECKING:
    from eidos_runtime.application.automations import AutomationApplication
    from eidos_runtime.model.client import ModelToolCall
    from eidos_runtime.runtime.tool_runtime import ToolCallRuntime
    from eidos_runtime.runtime.tool_execution import HandlerOutcome


class AutomationData(StrictToolModel):
    SUCCESS_REQUIRED: ClassVar[tuple[str, ...]] = ("response",)
    response: AutomationResponse | None = None


class AutomationAdapter:
    def execute(self, arguments: dict[str, object], cancel: threading.Event) -> dict[str, object]:
        raise RuntimeError("automation requires the Run runtime")


@dataclass(frozen=True)
class AutomationToolRuntime(AdapterToolRuntime):
    application: AutomationApplication | None = None

    def invocation_effect(self, arguments: dict[str, object]) -> str:
        request = AutomationRequest.model_validate(arguments)
        return "none" if request.mode in {"view", "preview"} else "eidos_state"

    def invoke(self, context: ToolCallRuntime, run_id: str, item: dict[str, object], call: ModelToolCall, cancel: threading.Event) -> HandlerOutcome:
        from eidos_runtime.runtime.tool_execution import HandlerOutcome, PreparedToolExecution
        if self.application is None:
            raise RuntimeError("automation application unavailable")
        try:
            request = AutomationRequest.model_validate(call.arguments)
            operation_id = str(uuid5(NAMESPACE_URL, "automation/tool/" + str(item["id"])))
            session_id = str(context.store.read_run(run_id)["sessionId"])
            if request.mode not in {"view", "preview"}:
                self.application.execute(request, context_session_id=session_id, run_id=run_id,
                    operation_id=operation_id, cancel=cancel, prepare_only=True)
                context.controller.authorize_workspace_side_effect(item=item, prepared=PreparedToolExecution(
                    approval_description={}, intent_preconditions={"automation": request.to_wire_dict(exclude_none=True)},
                    transition_reason="automation_mutation",
                ))
                context.controller.mark_execution_started()
            response = self.application.execute(request,
                context_session_id=session_id, run_id=run_id, operation_id=operation_id, cancel=cancel)
            return HandlerOutcome(tool_result(call.name, "success", "automation_" + request.mode,
                "定时任务已保存。" if response.changed else "定时任务信息已读取。",
                {"response": response.to_wire_dict()}, data_model=AutomationData,
                side_effects_may_exist=response.changed or response.replayed, reconciliation_required=False), "completed", "completed")
        except (AutomationError, ValidationError, ApplicationError) as error:
            code = error.code if isinstance(error, (AutomationError, ApplicationError)) else "invalid_arguments"
            return HandlerOutcome(tool_result(call.name, "error", code, str(error), data_model=AutomationData,
                side_effects_may_exist=False, reconciliation_required=False), "failed", "failed")


def automation_entry(application: AutomationApplication, *, self_pause_only: bool = False) -> ToolRegistryEntry:
    schema = AutomationRequest.model_json_schema(by_alias=True)
    if self_pause_only:
        from eidos_runtime.domain.automation import Revision
        from eidos_runtime.models.base import EidosFrozenStrictModel
        from typing import Literal

        class PauseChanges(EidosFrozenStrictModel):
            status: Literal["PAUSED"]

        class PauseRequest(EidosFrozenStrictModel):
            mode: Literal["update"]
            id: str
            expected_revision: Revision
            changes: PauseChanges

        input_model = PauseRequest
        schema = PauseRequest.model_json_schema(by_alias=True)
        description = "Pause this scheduled task after its stop condition is met. Only update changes.status=PAUSED for the automation ID and revision provided by Runtime."
    else:
        input_model = AutomationRequest
        description = (
            "Manage local scheduled tasks. mode=view with id reads details; without id lists tasks. "
            "create saves a future task: kind=at or cron, name, prompt, schedule. "
            "sessionId reuses an existing conversation; omit it to create a new conversation per execution. "
            "projectId=null means projectless; omitted projectId inherits the current project. "
            "approvalMode defaults to auto_review. update requires id, expectedRevision and changes; "
            "omitted changes preserve saved values. delete soft-deletes. preview only calculates times. "
            "Fixed intervals are 10/30/60 minutes. Never invent IDs or claim success before receiving the result. "
            "定时任务、计划、提醒、暂停、查询。"
        )
    spec = ToolSpec(name="automation", description=description, sideEffect="eidos_state", approvalRequired=False,
        timeoutSeconds=30, batchPolicy="single", visibility="deferred", inputSchema=schema,
        resultSchema=result_model(AutomationData).model_json_schema(by_alias=True))
    provenance = ToolProvenance(kind="builtin", sourceId="eidos.automation", sourceVersion="1",
        contentHash=hashlib.sha256(spec.model_dump_json().encode()).hexdigest())
    adapter = AutomationAdapter()
    return ToolRegistryEntry(spec, provenance, adapter, input_model, AutomationData,
        runtime=AutomationToolRuntime(adapter, spec, provenance, application=application), preserve_omitted_arguments=True)
