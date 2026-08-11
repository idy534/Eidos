from __future__ import annotations

from collections.abc import Mapping

from eidos_runtime.db.database import CommittedMutation, Database, Repository
from eidos_runtime.db.errors import ResourceNotFoundError, StorageError
from eidos_runtime.domain.execution import (
    Item,
    ModelAttempt,
    Step,
)
from eidos_runtime.domain.run import Run
from eidos_runtime.domain.session import (
    DeletedSession,
    Session,
    SessionPage,
    SessionProjection,
    SessionProjectionPage,
)
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
    """Typed application-facing repository over the existing SQLite authority.

    The existing repositories retain their SQL and state-transition ownership.
    This facade gives application services typed reads and selected typed
    writes without moving a transaction boundary or exposing wire dictionaries
    at the new public surface.  Legacy ``SessionStore`` methods remain intact
    for callers that have not migrated yet.
    """

    def __init__(self, database: Database) -> None:
        super().__init__(database)
        # These modules import persistence mappers, so importing them lazily
        # avoids turning the mapper package into an import cycle.
        from eidos_runtime.db.repositories.execution import ExecutionRepository
        from eidos_runtime.db.repositories.runs import RunRepository
        from eidos_runtime.db.repositories.sessions import SessionRepository

        self._sessions = SessionRepository(database)
        self._runs = RunRepository(database)
        self._execution = ExecutionRepository(database)

    def create_session(
        self,
        workspace_root: str,
        *,
        worktree_id: str | None = None,
        operation_id: str | None = None,
    ) -> CommittedMutation[Session]:
        return self._sessions.create_session_committed(
            workspace_root,
            worktree_id=worktree_id,
            operation_id=operation_id,
        )

    def list_sessions(
        self, *, limit: int = 50, cursor: str | None = None
    ) -> SessionPage:
        return self._sessions.list_sessions(limit=limit, cursor=cursor)

    def list_session_projections(
        self, *, limit: int = 50, cursor: str | None = None
    ) -> SessionProjectionPage:
        return self._sessions.list_session_projections(limit=limit, cursor=cursor)

    def read_session(self, session_id: str) -> Session | None:
        return self._sessions.read_session(session_id)

    def read_session_projection(self, session_id: str) -> SessionProjection | None:
        return self._sessions.read_session_projection(session_id)

    def rename_session(
        self,
        session_id: str,
        title: str,
        *,
        operation_id: str | None = None,
    ) -> CommittedMutation[Session]:
        return self._sessions.rename_session_committed(
            session_id, title, operation_id=operation_id
        )

    def delete_session(
        self,
        session_id: str,
        *,
        operation_id: str | None = None,
    ) -> CommittedMutation[DeletedSession]:
        return self._sessions.delete_session_committed(
            session_id, operation_id=operation_id
        )

    def assert_session_deletable(self, session_id: str) -> None:
        self._sessions.assert_session_deletable(session_id)

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

    def claim_next_run_committed(self) -> CommittedMutation[Run] | None:
        """Claim the next queued Run without leaking the legacy wire record."""

        with self.lock:
            mutation = self._runs.claim_next_run_committed()
            if mutation is None:
                return None
            return self._typed_run_mutation(mutation)

    def request_cancel_committed(self, run_id: str) -> CommittedMutation[Run]:
        with self.lock:
            return self._typed_run_mutation(
                self._runs.request_cancel_committed(run_id)
            )

    def complete_requested_cancel_committed(
        self, run_id: str
    ) -> CommittedMutation[Run]:
        with self.lock:
            return self._typed_run_mutation(
                self._runs.complete_requested_cancel_committed(run_id)
            )

    def cancel_run_committed(self, run_id: str) -> CommittedMutation[Run]:
        with self.lock:
            return self._typed_run_mutation(
                self._runs.cancel_run_committed(run_id)
            )

    def interrupt_run_committed(self, run_id: str) -> CommittedMutation[Run]:
        with self.lock:
            return self._typed_run_mutation(
                self._runs.interrupt_run_committed(run_id)
            )

    def begin_approval_committed(
        self,
        item_id: str,
        diff: str,
        base_sha256: str | None,
        *,
        request: dict[str, object] | None = None,
        attempt_ordinal: int = 0,
        approval_kind: str = "tool",
    ) -> CommittedMutation[Approval]:
        with self.lock:
            mutation = self._execution.begin_approval_committed(
                item_id,
                diff,
                base_sha256,
                request=request,
                attempt_ordinal=attempt_ordinal,
                approval_kind=approval_kind,
            )
            return self._typed_approval_mutation(item_id, mutation.events)

    def resolve_approval_committed(
        self,
        item_id: str,
        decision: str,
        feedback: str | None,
        *,
        requeue: bool = False,
    ) -> CommittedMutation[Approval]:
        """Resolve one approval while preserving its event/outbox transaction."""

        with self.lock:
            mutation = self._execution.resolve_approval_committed(
                item_id, decision, feedback, requeue=requeue
            )
            return self._typed_approval_mutation(item_id, mutation.events)

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

    def _typed_run_mutation(
        self, mutation: CommittedMutation[object]
    ) -> CommittedMutation[Run]:
        value = mutation.value
        if not isinstance(value, Mapping) or not isinstance(value.get("id"), str):
            raise StorageError("run mutation result is invalid")
        run = self._read_run_locked(value["id"])
        return CommittedMutation(run, mutation.events)

    def _typed_approval_mutation(
        self,
        item_id: str,
        events: tuple[dict[str, object], ...],
    ) -> CommittedMutation[Approval]:
        row = self._connection().execute(
            """
            SELECT * FROM approvals
            WHERE item_id = ?
            ORDER BY creation_seq DESC
            LIMIT 1
            """,
            (item_id,),
        ).fetchone()
        if row is None:
            raise ResourceNotFoundError("approval not found")
        return CommittedMutation(approval_from_row(row), events)

    def _read_run_locked(self, run_id: str) -> Run:
        row = self._connection().execute(
            "SELECT * FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise ResourceNotFoundError("run not found")
        return run_from_row(row)


__all__ = ["TypedRuntimeRepository"]
