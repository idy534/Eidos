from __future__ import annotations

from collections.abc import Callable
from threading import Lock

from opentelemetry import context as otel_context
from opentelemetry.context import Context

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.collaboration import (
    CollaborationRejected, AgentMessageRequest, AgentSummary, AgentSuspended, CollaborationState,
    SpawnAgent, WaitAgents,
)
from eidos_runtime.persistence.collaboration import CollaborationRepository


class CollaborationApplication:
    """Coordinates durable delegation through the existing Run scheduler."""

    def __init__(self, store: SessionStore, schedule: Callable[[], None],
                 cancel: Callable[[str], object], publish: Callable[[], object]):
        self.repository = CollaborationRepository(store.database)
        self.schedule = schedule
        self.cancel = cancel
        self.publish = publish
        self._trace_contexts: dict[str, Context] = {}
        self._trace_lock = Lock()

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
            return self.stop(parent_run_id, agent_id)
        except CollaborationRejected as error:
            raise ApplicationError('INVALID_STATE', str(error)) from error

    def spawn(self, run_id: str, item_id: str, request: SpawnAgent) -> AgentSummary:
        result = self.repository.spawn(run_id, item_id, request)
        self._remember_trace_context(result.run_id)
        self.publish()
        self.schedule()
        return result

    def send(self, run_id: str, item_id: str, request: AgentMessageRequest) -> None:
        self.repository.send(run_id, item_id, request.agent_id, request.message)
        self.publish()

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

    def wait(self, run_id: str, item_id: str | None, request: WaitAgents) -> CollaborationState:
        if self.repository.wait(run_id, item_id, request):
            self.publish()
            self.schedule()
            raise AgentSuspended()
        return self.repository.state(run_id)
