from __future__ import annotations

from collections.abc import Callable
from threading import Event, Lock
import logging
import time

from opentelemetry import context as otel_context
from opentelemetry.context import Context

from eidos_runtime.application.agent_catalog import AgentCatalog
from eidos_runtime.application.agent_workspaces import AgentWorkspaceError, AgentWorkspaceService
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from eidos_runtime.application.teams import TeamApplication
from eidos_runtime.model.config import ModelConfigError
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.collaboration import (
    AgentChangedFile, AgentChanges, ReviewAgentChanges, CollaborationRejected, AgentMessageRequest, AgentSummary, AgentSuspended, AgentWaitOutcome,
    CollaborationState, SpawnAgent, WaitAgents,
)
from eidos_runtime.persistence.collaboration import CollaborationRepository
from eidos_runtime.runtime.contracts import RuntimeCancelled


logger = logging.getLogger("eidos.runtime")


class CollaborationApplication:
    """Coordinates durable delegation through the existing Run scheduler."""

    def __init__(self, store: SessionStore, schedule: Callable[[], None],
                 cancel: Callable[[str], object], publish: Callable[[], object]):
        self.store = store
        self.repository = CollaborationRepository(store.database)
        self.schedule = schedule
        self.cancel = cancel
        self.publish = publish
        self._trace_contexts: dict[str, Context] = {}
        self._trace_lock = Lock()
        self.workspaces: AgentWorkspaceService | None = None
        self.teams: TeamApplication | None = None

    def bind_workspaces(self, workspaces: AgentWorkspaceService | None) -> None:
        self.workspaces = workspaces

    def take_trace_context(self, run_id: str) -> Context | None:
        with self._trace_lock:
            return self._trace_contexts.pop(run_id, None)

    def _remember_trace_context(self, run_id: str) -> None:
        with self._trace_lock:
            self._trace_contexts[run_id] = otel_context.get_current()

    def read_session(self, session_id: str) -> CollaborationState:
        return self.repository.read_session(session_id)

    def stop_from_desktop(self, parent_run_id: str, agent_id: str) -> CollaborationState:
        from eidos_runtime.application.errors import ApplicationError
        try:
            self.stop(parent_run_id, agent_id)
            return self.repository.state(parent_run_id, include_usage=True)
        except CollaborationRejected as error:
            raise ApplicationError('INVALID_STATE', str(error)) from error

    def message_from_desktop(self, parent_run_id: str, agent_id: str, operation_id: str, text: str) -> CollaborationState:
        from eidos_runtime.application.errors import ApplicationError
        try:
            self.repository.user_message(parent_run_id, agent_id, operation_id, text)
        except CollaborationRejected as error:
            code = 'OPERATION_ID_REUSED' if str(error) == 'agent_operation_conflict' else 'INVALID_STATE'
            raise ApplicationError(code, str(error)) from error
        self.publish()
        self.schedule()
        return self.repository.state(parent_run_id, include_usage=True)

    def review_changes(self, run_id: str, request: ReviewAgentChanges) -> AgentChanges:
        """Changes of an idle isolated child, shared by the tool and Desktop."""
        workspaces = self.workspaces
        if workspaces is None:
            raise CollaborationRejected('agent_worktree_unavailable')
        try:
            delivery = workspaces.capture(run_id, request.agent_id)
            if request.path is not None and request.path not in {entry.path for entry in delivery.entries}:
                raise CollaborationRejected('agent_change_path_unknown')
            diff, next_offset, has_more = workspaces.diff_page(
                delivery, path=request.path, offset=request.offset_bytes, max_bytes=request.max_bytes)
        except AgentWorkspaceError as error:
            raise CollaborationRejected(error.code) from error
        return AgentChanges(agent_id=request.agent_id, status=delivery.status, files=[
            AgentChangedFile(path=entry.path, kind=entry.kind, additions=entry.additions,
                             deletions=entry.deletions, binary=entry.binary) for entry in delivery.entries],
            diff=diff, diff_next_offset=next_offset, diff_has_more=has_more)

    def changes_from_desktop(self, request: ReviewAgentChanges, parent_run_id: str) -> AgentChanges:
        from eidos_runtime.application.errors import ApplicationError
        try:
            return self.review_changes(parent_run_id, request)
        except CollaborationRejected as error:
            raise ApplicationError('INVALID_STATE', str(error)) from error

    def discard_from_desktop(self, parent_run_id: str, agent_id: str) -> CollaborationState:
        from eidos_runtime.application.errors import ApplicationError
        if self.workspaces is None:
            raise ApplicationError('INVALID_STATE', 'agent_worktree_unavailable')
        try:
            self.workspaces.discard(parent_run_id, agent_id)
        except AgentWorkspaceError as error:
            raise ApplicationError('INVALID_STATE', error.code) from error
        self.publish()
        return self.repository.state(parent_run_id, include_usage=True)

    def spawn(self, run_id: str, item_id: str, request: SpawnAgent) -> AgentSummary:
        replay = self.repository.replay_spawn(run_id, item_id, request)
        if replay is not None:
            return replay
        parent_session_id = self.repository.check_spawn(run_id, request)
        definition = self.repository.definition_for_spawn(run_id, request.role)
        profile = None
        if definition is not None:
            try:
                directory = self.store.database.data_directory
                if directory is None:
                    raise CollaborationRejected("agent_run_not_active")
                profile = AgentCatalog(directory).resolve_model(definition, self.store.read_model_profile(run_id))
            except (ValueError, ModelConfigError) as error:
                raise CollaborationRejected('agent_model_unavailable') from error
        prepared = None
        if request.workspace == 'worktree':
            if self.workspaces is None:
                raise CollaborationRejected('agent_worktree_unavailable')
            try:
                prepared = self.workspaces.prepare(parent_session_id, item_id)
            except AgentWorkspaceError as error:
                raise CollaborationRejected(error.code) from error
        try:
            result = self.repository.spawn(run_id, item_id, request, model_profile=profile, workspace=prepared)
        except Exception:
            if prepared is not None and self.workspaces is not None:
                # Nothing owns the new Worktree. Other spawns may be in flight,
                # so only this one is dropped.
                self.workspaces.abandon(prepared)
            raise
        if prepared is not None and self.workspaces is not None:
            self.workspaces.pin_base(prepared, result.id)
            self.workspaces.complete(prepared)
        self._remember_trace_context(result.run_id)
        self.publish()
        self.schedule()
        return result

    def send(self, run_id: str, item_id: str, request: AgentMessageRequest) -> None:
        self.repository.send(run_id, item_id, request.agent_id, request.message)
        self.publish()

    def stop_optional(self, run_id: str) -> None:
        from eidos_runtime.domain.collaboration import ACTIVE_STATUSES

        for agent in self.repository.state(run_id).agents:
            if agent.parent_run_id == run_id and not agent.required_for_completion and agent.status.value in ACTIVE_STATUSES:
                self.stop(run_id, agent.id)

    def followup(self, run_id: str, item_id: str, request: AgentMessageRequest) -> AgentSummary:
        result = self.repository.followup(run_id, item_id, request.agent_id, request.message)
        self._remember_trace_context(result.run_id)
        self.publish()
        self.schedule()
        return result

    def stop(self, run_id: str, agent_id: str) -> CollaborationState:
        self.cancel(self.repository.target_run(run_id, agent_id))
        self.publish()
        self.schedule()
        return self.repository.state(run_id)

    def wait(self, run_id: str, item_id: str | None, request: WaitAgents, *,
             cancel: Event | None = None, keep_worker: bool = False,
             interruptible: bool = True) -> tuple[CollaborationState, AgentWaitOutcome]:
        """Wait for children, or return early for a parent message or user input.

        Internal loops that cannot consume messages or input pass
        interruptible=False so they do not return to the same wait at once.
        """
        if keep_worker:
            # A live Shell belongs to this Worker. Durable suspension would
            # close RunResources and kill it. Keep only its owner alive; never
            # hold a DB transaction or a shared execution permit while waiting.
            if cancel is None:
                raise ValueError('resource-owning wait requires cancellation')
            deadline = time.monotonic() + request.timeout_ms / 1000
            targets = self.repository.wait_targets(run_id, request)
            logger.info("Agent wait retains live resources run_id=%s timeout_ms=%s", run_id, request.timeout_ms)
            while True:
                if cancel.is_set():
                    logger.info("Resource-owning agent wait canceled run_id=%s", run_id)
                    raise RuntimeCancelled
                remaining = deadline - time.monotonic()
                state, outcome = self.repository.inspect_wait(
                    run_id, item_id, targets, request.mode, remaining <= 0, interruptible)
                if outcome is not None:
                    logger.info("Resource-owning agent wait finished run_id=%s reason=%s", run_id, outcome.reason)
                    return state, outcome
                cancel.wait(min(0.1, remaining))
        outcome = self.repository.wait(run_id, item_id, request)
        if outcome is None:
            self.publish()
            self.schedule()
            raise AgentSuspended()
        return self.repository.state(run_id), outcome
