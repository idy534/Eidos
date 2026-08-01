from __future__ import annotations

from eidos_runtime.db.database import Database, Repository
from eidos_runtime.domain.execution import (
    Item,
    ModelAttempt,
    Step,
)
from eidos_runtime.domain.run import Run
from eidos_runtime.domain.tool import Approval, ToolCall
from eidos_runtime.persistence.mappers.runtime import (
    approval_from_row,
    item_from_row,
    model_attempt_from_row,
    run_from_row,
    step_from_row,
    tool_call_from_row,
)


class TypedRuntimeRepository(Repository):
    """Read-side typed seam for records still owned by legacy repositories.

    The existing repositories remain the write authority.  This small seam
    makes the migration incremental: callers can consume validated domain
    records without receiving ``sqlite3.Row`` or wire dictionaries, while the
    durable state machine and transaction boundaries remain unchanged.
    """

    def __init__(self, database: Database) -> None:
        super().__init__(database)

    def read_run(self, run_id: str) -> Run | None:
        with self.lock:
            row = self._connection().execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
        return run_from_row(row) if row is not None else None

    def list_runs(self, session_id: str) -> tuple[Run, ...]:
        with self.lock:
            rows = self._connection().execute(
                """
                SELECT * FROM runs
                WHERE session_id = ?
                ORDER BY creation_seq ASC
                """,
                (session_id,),
            ).fetchall()
        return tuple(run_from_row(row) for row in rows)

    def read_item(self, item_id: str) -> Item | None:
        with self.lock:
            row = self._connection().execute(
                "SELECT * FROM items WHERE id = ?", (item_id,)
            ).fetchone()
        return item_from_row(row) if row is not None else None

    def read_step(self, step_id: str) -> Step | None:
        with self.lock:
            row = self._connection().execute(
                "SELECT * FROM steps WHERE id = ?", (step_id,)
            ).fetchone()
        return step_from_row(row) if row is not None else None

    def read_tool_call(self, tool_call_id: str) -> ToolCall | None:
        with self.lock:
            row = self._connection().execute(
                "SELECT * FROM tool_calls WHERE id = ?", (tool_call_id,)
            ).fetchone()
        return tool_call_from_row(row) if row is not None else None

    def read_approval(self, approval_id: str) -> Approval | None:
        with self.lock:
            row = self._connection().execute(
                "SELECT * FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
        return approval_from_row(row) if row is not None else None

    def read_pending_approval(self, run_id: str) -> Approval | None:
        with self.lock:
            row = self._connection().execute(
                """
                SELECT * FROM approvals
                WHERE run_id = ? AND status = 'pending'
                ORDER BY creation_seq ASC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        return approval_from_row(row) if row is not None else None

    def list_model_attempts(self, run_id: str) -> tuple[ModelAttempt, ...]:
        with self.lock:
            rows = self._connection().execute(
                """
                SELECT model_attempts.*
                FROM model_attempts
                JOIN steps ON steps.id = model_attempts.step_id
                WHERE steps.run_id = ?
                ORDER BY model_attempts.creation_seq ASC
                """,
                (run_id,),
            ).fetchall()
        return tuple(model_attempt_from_row(row) for row in rows)


__all__ = ["TypedRuntimeRepository"]
