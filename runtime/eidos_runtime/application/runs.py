from __future__ import annotations

from eidos_runtime.db.database import Database
from eidos_runtime.domain.run import Run
from eidos_runtime.persistence.repositories import TypedRuntimeRepository


class RunApplication:
    """Typed read/use-case boundary for Run facts.

    Run transitions remain owned by the existing RunRepository and supervisor;
    this class deliberately does not duplicate those state decisions.
    """

    def __init__(self, database: Database) -> None:
        self.repository = TypedRuntimeRepository(database)

    def read(self, run_id: str) -> Run | None:
        return self.repository.read_run(run_id)

    def list(self, session_id: str) -> tuple[Run, ...]:
        return self.repository.list_runs(session_id)
