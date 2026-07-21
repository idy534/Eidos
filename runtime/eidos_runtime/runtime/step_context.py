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
from eidos_runtime.tools.registry import StepToolSnapshot


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
        tool_snapshot: StepToolSnapshot | None = None,
        context_budget: ContextBudget | None = None,
        workspace_version: int = 0,
    ) -> StepContext:
        dispatcher = resources.dispatcher
        if dispatcher is None:
            raise RuntimeError("run resources are not started")
        snapshot = tool_snapshot or dispatcher.snapshot(
            self.store.activated_tools(run.run_id)
        )
        step_index = self.store.increment_model_step(
            run.run_id, tool_snapshot=snapshot.as_dict()
        )
        fact = self.store.read_current_step_fact(run.run_id)
        workspace = self.store.workspace_for_run(run.run_id)
        return StepContext(
            run_id=run.run_id,
            session_id=run.session_id,
            step_id=str(fact["stepId"]),
            step_index=step_index,
            model_id=run.model_id,
            model_context=(
                model_context
                if model_context is not None
                else (*run.model_context, *run.skill_context)
            ),
            tool_snapshot=snapshot,
            tool_definitions=tuple(
                dispatcher.model_definitions(snapshot.activated_names)
            ),
            workspace_identity=WorkspaceIdentitySnapshot(
                path=str(workspace.path),
                device=workspace.device,
                inode=workspace.inode,
                owner=workspace.owner,
            ),
            reconciliation_epoch=int(fact["reconciliationEpoch"]),
            workspace_version=workspace_version,
            context_budget=context_budget,
            extension_snapshot_hash=run.extension_snapshot_hash,
        )
