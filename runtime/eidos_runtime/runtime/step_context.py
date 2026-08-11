from __future__ import annotations

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.runtime.contracts import (
    RunContext,
    StepContext,
    WorkspaceIdentitySnapshot,
)
from eidos_runtime.runtime.run_resources import RunResources
from eidos_runtime.context.budget import ContextBudget
from eidos_runtime.model.client import ModelContextItem
from eidos_runtime.model.prompts import ResolvedInstructions
from eidos_runtime.tools.registry import StepToolSnapshot
from eidos_runtime.runtime.resolution import (
    RuleResolutionSnapshot,
    create_step_resolution_snapshot,
)
import time


class StepContextFactory:
    """Captures one persisted Step and its immutable capability view."""

    def __init__(self, store: SessionStore) -> None:
        self.store = store

    def create(
        self,
        run: RunContext,
        resources: RunResources,
        *,
        model_context: tuple[ModelContextItem, ...] | None = None,
        instructions: ResolvedInstructions,
        tool_snapshot: StepToolSnapshot | None = None,
        rule_resolution_snapshot: RuleResolutionSnapshot,
        context_budget: ContextBudget | None = None,
        workspace_version: int = 0,
        new_user_input_ids: tuple[str, ...] = (),
    ) -> StepContext:
        dispatcher = resources.dispatcher
        if dispatcher is None:
            raise RuntimeError("run resources are not started")
        snapshot = tool_snapshot or dispatcher.snapshot(
            self.store.activated_tools(run.run_id)
        )
        effective_context = (
            model_context
            if model_context is not None
            else run.model_context
        )
        tool_definitions = tuple(
            dispatcher.model_definitions(snapshot.activated_names)
        )
        resolution = create_step_resolution_snapshot(
            run_snapshot=run.resolution_snapshot,
            rule_snapshot=rule_resolution_snapshot,
            tool_snapshot=snapshot.as_dict(),
            model_context=effective_context,
            tool_definitions=tool_definitions,
            instructions=instructions,
            workspace_version=workspace_version,
            effective_cwd=rule_resolution_snapshot.cwd,
            created_at=time.time_ns() // 1_000_000,
        )
        step_index = self.store.increment_model_step(
            run.run_id,
            tool_snapshot=snapshot.as_dict(),
            rule_resolution_snapshot=rule_resolution_snapshot,
            resolution_snapshot=resolution,
        )
        fact = self.store.read_current_step_fact(run.run_id)
        workspace = self.store.workspace_for_run(run.run_id)
        return StepContext(
            run_id=run.run_id,
            session_id=run.session_id,
            step_id=str(fact["stepId"]),
            step_index=step_index,
            model_id=run.model_id,
            model_profile=run.model_profile,
            model_context=effective_context,
            instructions=instructions,
            tool_snapshot=snapshot,
            tool_definitions=tool_definitions,
            workspace_identity=WorkspaceIdentitySnapshot(
                path=str(workspace.path),
                device=workspace.device,
                inode=workspace.inode,
                owner=workspace.owner,
                git_dir=(
                    str(workspace.git_dir)
                    if workspace.git_dir is not None
                    else None
                ),
                git_common_dir=(
                    str(workspace.git_common_dir)
                    if workspace.git_common_dir is not None
                    else None
                ),
            ),
            reconciliation_epoch=int(fact["reconciliationEpoch"]),
            workspace_version=workspace_version,
            context_budget=context_budget,
            extension_snapshot_hash=run.extension_snapshot_hash,
            resolution_snapshot=resolution,
            new_user_input_ids=new_user_input_ids,
        )
