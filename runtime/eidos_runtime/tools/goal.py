from __future__ import annotations

from dataclasses import dataclass
import hashlib
import threading
from typing import TYPE_CHECKING, ClassVar

from eidos_runtime.domain.goal import GoalDeclaration, GoalError
from eidos_runtime.persistence.goals import GoalRepository
from eidos_runtime.runtime.errors import tool_result
from eidos_runtime.tools.contracts import StrictToolModel, result_model
from eidos_runtime.tools.registry import AdapterToolRuntime, ToolProvenance, ToolRegistryEntry, ToolSpec

if TYPE_CHECKING:
    from eidos_runtime.model.client import ModelToolCall
    from eidos_runtime.runtime.tool_execution import HandlerOutcome
    from eidos_runtime.runtime.tool_runtime import ToolCallRuntime


class GoalData(StrictToolModel):
    SUCCESS_REQUIRED: ClassVar[tuple[str, ...]] = ("pending",)
    pending: bool | None = None


class GoalAdapter:
    def execute(self, arguments: dict[str, object], cancel: threading.Event) -> dict[str, object]:
        raise RuntimeError("update_goal requires the Run runtime")


@dataclass(frozen=True)
class GoalToolRuntime(AdapterToolRuntime):
    repository: GoalRepository

    def invoke(self, context: ToolCallRuntime, run_id: str, item: dict[str, object], call: ModelToolCall, cancel: threading.Event) -> HandlerOutcome:
        from eidos_runtime.runtime.tool_execution import HandlerOutcome, PreparedToolExecution
        declaration = GoalDeclaration.model_validate(call.arguments)
        try:
            self.repository.declare(run_id, str(item["id"]), declaration, prepare_only=True)
            context.controller.authorize_workspace_side_effect(item=item, prepared=PreparedToolExecution(
                approval_description={}, intent_preconditions={"goalDeclaration": declaration.to_wire_dict()},
                transition_reason="goal_declaration",
            ))
            context.controller.mark_execution_started()
            # ExecutionRepository revalidates and saves the candidate in the
            # same transaction as this ToolResult. It is not completion yet.
            return HandlerOutcome(tool_result(call.name, "success", "goal_declaration_recorded",
                "目标声明已记录。Run 通过最终检查后，Runtime 才会确认目标状态。", {"pending": True},
                data_model=GoalData, side_effects_may_exist=True, reconciliation_required=False), "completed")
        except GoalError as error:
            return HandlerOutcome(tool_result(call.name, "error", error.code, str(error), data_model=GoalData,
                side_effects_may_exist=False, reconciliation_required=False), "failed", "failed")


def goal_entry(repository: GoalRepository) -> ToolRegistryEntry:
    spec = ToolSpec(name="update_goal", description=(
        "Declare the current goal complete or blocked. Completion requires a summary and evidence for every requirement. "
        "Blocked requires the SAME obstacle in at least three consecutive goal Runs in the current activation. "
        "Difficulty, uncertainty, slow progress or a wish to clarify do not justify either declaration. "
        "Only the user can pause, resume, clear, create or edit goals. The declaration is pending until final Run checks pass. "
        "Make this the last tool after collecting current evidence. If the objective changed, audit the new objective first."
    ), sideEffect="eidos_state", approvalRequired=False, timeoutSeconds=30, batchPolicy="single",
        visibility="direct", inputSchema=GoalDeclaration.model_json_schema(by_alias=True),
        resultSchema=result_model(GoalData).model_json_schema(by_alias=True))
    provenance = ToolProvenance(kind="builtin", sourceId="eidos.goal", sourceVersion="1",
        contentHash=hashlib.sha256(spec.model_dump_json().encode()).hexdigest())
    adapter = GoalAdapter()
    return ToolRegistryEntry(spec, provenance, adapter, GoalDeclaration, GoalData,
        runtime=GoalToolRuntime(adapter, spec, provenance, repository=repository))
