from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pydantic import Field
from typing import ClassVar, TYPE_CHECKING
import threading

from eidos_runtime.domain.planning import (
    RequestUserInput, UserInputResponse, WritePlan, PlanningSuspended,
)
from eidos_runtime.persistence.planning import PlanningRepository, PlanWriteRejected
from eidos_runtime.runtime.errors import tool_result
from eidos_runtime.tools.contracts import StrictToolModel, result_model
from eidos_runtime.tools.registry import AdapterToolRuntime, ToolProvenance, ToolRegistryEntry, ToolSpec


if TYPE_CHECKING:
    from eidos_runtime.model.client import ModelToolCall
    from eidos_runtime.runtime.tool_runtime import ToolCallRuntime
    from eidos_runtime.runtime.tool_execution import HandlerOutcome


class InputResultData(StrictToolModel):
    SUCCESS_REQUIRED: ClassVar[tuple[str, ...]] = ('response',)
    response: UserInputResponse | None = None


class PlanResultData(StrictToolModel):
    SUCCESS_REQUIRED: ClassVar[tuple[str, ...]] = ('plan_id', 'revision', 'path', 'sha256')
    plan_id: str | None = Field(default=None, alias="planId")
    revision: int | None = None
    path: str | None = None
    sha256: str | None = None
    title: str | None = None


class PlanningAdapter:
    def execute(self, arguments: dict[str, object], cancel: threading.Event) -> dict[str, object]:
        raise RuntimeError('planning tools require the Run runtime')


@dataclass(frozen=True)
class PlanningToolRuntime(AdapterToolRuntime):
    def invoke(self, context: ToolCallRuntime, run_id: str, item: dict[str, object], call: ModelToolCall, cancel: threading.Event) -> HandlerOutcome:
        from eidos_runtime.runtime.tool_execution import HandlerOutcome, PreparedToolExecution
        repository = PlanningRepository(context.store.database)
        if context.store.read_run(run_id).get('workMode') != 'plan':
            raise ValueError('plan_run_required')
        if call.name == 'request_user_input':
            saved = repository.for_item(str(item['id']))
            if saved is not None and saved.response is not None:
                return HandlerOutcome(tool_result(call.name, 'success', 'user_input_received',
                    'The user responded.', {'response': saved.response.to_wire_dict()}, data_model=InputResultData), 'completed', 'completed')
            if saved is None:
                if context.concurrency.has_managed_shell:
                    raise ValueError('finish_running_shell_before_requesting_input')
                repository.ask(run_id, str(item['id']), RequestUserInput.model_validate(call.arguments))
                context.events.deliver_pending()
            raise PlanningSuspended()
        request = WritePlan.model_validate(call.arguments)
        if request.ready_for_review:
            from eidos_runtime.persistence.collaboration import CollaborationRepository
            if CollaborationRepository(context.store.database).child_runs(run_id):
                return HandlerOutcome(tool_result(call.name, 'error', 'agents_still_active',
                    'Wait for or stop child tasks before submitting the final plan.',
                    data_model=PlanResultData), 'failed', 'failed')
        try:
            repository.validate_write(run_id, request)
            context.controller.authorize_workspace_side_effect(item=item, prepared=PreparedToolExecution(
                approval_description={}, intent_preconditions={'planId': request.plan_id, 'expectedRevision': request.expected_revision},
                transition_reason='plan_document_write',
            ))
            document = repository.write(run_id, request)
        except PlanWriteRejected as error:
            return HandlerOutcome(tool_result(call.name, 'error', str(error),
                'Plan was not changed. For a new plan omit planId and expectedRevision; for an existing plan use its returned ID and current revision.',
                data_model=PlanResultData), 'failed', 'failed')
        return HandlerOutcome(tool_result(call.name, 'success', 'plan_ready' if request.ready_for_review else 'plan_saved',
            'Plan saved. Wait for user confirmation before implementation.' if request.ready_for_review else 'Plan draft saved.',
            {'planId': document.id, 'revision': document.revision, 'path': document.path, 'sha256': document.sha256, 'title': document.title}, data_model=PlanResultData), 'completed', 'completed')


def planning_entries() -> tuple[ToolRegistryEntry, ...]:
    entries = []
    for name, description, input_model, output_model in (
        ('request_user_input', 'Request user input for one to three short questions and wait for the response. This tool is only available in Plan mode.', RequestUserInput, InputResultData),
        ('write_plan', 'Save a Markdown plan outside the project. Include the goal, findings, clarified decisions, implementation steps and verification. For a new plan, omit planId and expectedRevision; Eidos generates the ID. When revising, supply the exact planId and current revision returned by write_plan as expectedRevision. Never invent an ID. Set readyForReview to submit the complete plan and end this turn. This tool is only available in Plan mode.', WritePlan, PlanResultData),
    ):
        spec = ToolSpec(name=name, description=description, sideEffect='eidos_state' if name == 'write_plan' else 'none', approvalRequired=False,
            timeoutSeconds=60, inputSchema=input_model.model_json_schema(by_alias=True),
            resultSchema=result_model(output_model).model_json_schema(by_alias=True))
        provenance = ToolProvenance(kind='builtin', sourceId='eidos.planning', sourceVersion='1',
            contentHash=hashlib.sha256(spec.model_dump_json().encode()).hexdigest())
        adapter = PlanningAdapter()
        entries.append(ToolRegistryEntry(spec, provenance, adapter, input_model, output_model,
            runtime=PlanningToolRuntime(adapter, spec, provenance)))
    return tuple(entries)
