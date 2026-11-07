from __future__ import annotations

from collections.abc import Callable

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.goal import GoalView
from eidos_runtime.domain.automation import AutomationModel
from eidos_runtime.persistence.goals import GoalRepository
from eidos_runtime.protocol.goals import GoalRequest


class GoalApplication:
    def __init__(self, store: SessionStore, *, resolve_model: Callable[[AutomationModel], AutomationModel], changed: Callable[[], None]) -> None:
        self.repository = GoalRepository(store.database)
        self.resolve_model = resolve_model
        self.changed = changed

    def execute(self, request: GoalRequest) -> GoalView:
        if request.model is not None:
            request = request.model_copy(update={"model": self.resolve_model(request.model)})
        result = self.repository.execute(request)
        if request.action != "get":
            self.changed()
        return result
