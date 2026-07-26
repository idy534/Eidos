from __future__ import annotations

import hashlib
import json
import uuid

from pydantic import BaseModel, ConfigDict

from eidos_runtime.db.database import Repository, now_ms as _now_ms
from eidos_runtime.db.errors import (
    InvalidRunStateError,
    OperationConflictError,
)


class AsyncOperation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    id: str
    request_id: str | None
    operation_id: str
    scope: str
    request_hash: str
    status: str
    result: dict[str, object] | None = None
    error_code: str | None = None
    created_at: int
    started_at: int | None = None
    completed_at: int | None = None

    @property
    def terminal(self) -> bool:
        return self.status in {
            "completed",
            "failed",
            "canceled",
            "interrupted",
        }


class AsyncOperationRepository(Repository):
    def accept(
        self,
        *,
        request_id: str | None,
        operation_id: str,
        scope: str,
        request: dict[str, object],
    ) -> tuple[AsyncOperation, bool]:
        request_hash = hashlib.sha256(
            json.dumps(
                request,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        with self.lock, self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM async_operations
                WHERE scope = ? AND operation_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (scope, operation_id),
            ).fetchone()
            if row is not None:
                if row["request_hash"] != request_hash:
                    raise OperationConflictError(
                        "async operation payload conflict"
                    )
                return _operation_from_row(row), False
            operation = AsyncOperation(
                id=str(uuid.uuid4()),
                request_id=request_id,
                operation_id=operation_id,
                scope=scope,
                request_hash=request_hash,
                status="accepted",
                created_at=_now_ms(),
            )
            connection.execute(
                """
                INSERT INTO async_operations (
                    id, request_id, operation_id, scope, request_hash,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, 'accepted', ?)
                """,
                (
                    operation.id,
                    operation.request_id,
                    operation.operation_id,
                    operation.scope,
                    operation.request_hash,
                    operation.created_at,
                ),
            )
        return operation, True

    def read(self, operation_id: str) -> AsyncOperation | None:
        with self.lock:
            row = self._connection().execute(
                "SELECT * FROM async_operations WHERE id = ?",
                (operation_id,),
            ).fetchone()
        return _operation_from_row(row) if row is not None else None

    def start(self, operation_id: str) -> AsyncOperation:
        return self._transition(
            operation_id, ("accepted",), "running"
        )

    def complete(
        self, operation_id: str, result: dict[str, object]
    ) -> AsyncOperation:
        return self._transition(
            operation_id,
            ("accepted", "running"),
            "completed",
            result=result,
        )

    def fail(
        self, operation_id: str, error_code: str
    ) -> AsyncOperation:
        return self._transition(
            operation_id,
            ("accepted", "running"),
            "failed",
            error_code=error_code,
        )

    def cancel(self, operation_id: str) -> AsyncOperation:
        current = self.read(operation_id)
        if current is None:
            raise InvalidRunStateError("async operation is unavailable")
        if current.terminal:
            return current
        return self._transition(
            operation_id, ("accepted", "running"), "canceled"
        )

    def cancel_active(self) -> tuple[AsyncOperation, ...]:
        now = _now_ms()
        with self.lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id FROM async_operations
                WHERE status IN ('accepted', 'running')
                ORDER BY created_at
                """
            ).fetchall()
            connection.execute(
                """
                UPDATE async_operations
                SET status = 'canceled', completed_at = ?
                WHERE status IN ('accepted', 'running')
                """,
                (now,),
            )
        return tuple(
            operation
            for row in rows
            if (operation := self.read(str(row["id"]))) is not None
        )

    def _transition(
        self,
        operation_id: str,
        expected: tuple[str, ...],
        target: str,
        *,
        result: dict[str, object] | None = None,
        error_code: str | None = None,
    ) -> AsyncOperation:
        now = _now_ms()
        placeholders = ",".join("?" for _ in expected)
        with self.lock, self._connection() as connection:
            update = connection.execute(
                f"""
                UPDATE async_operations
                SET status = ?,
                    result_json = ?,
                    error_code = ?,
                    started_at = CASE
                        WHEN ? = 'running' THEN COALESCE(started_at, ?)
                        ELSE started_at
                    END,
                    completed_at = CASE
                        WHEN ? IN (
                            'completed', 'failed', 'canceled', 'interrupted'
                        ) THEN ?
                        ELSE completed_at
                    END
                WHERE id = ? AND status IN ({placeholders})
                """,
                (
                    target,
                    (
                        json.dumps(
                            result,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        )
                        if result is not None
                        else None
                    ),
                    error_code,
                    target,
                    now,
                    target,
                    now,
                    operation_id,
                    *expected,
                ),
            )
            if update.rowcount != 1:
                current = connection.execute(
                    "SELECT * FROM async_operations WHERE id = ?",
                    (operation_id,),
                ).fetchone()
                if current is not None and current["status"] == target:
                    return _operation_from_row(current)
                raise InvalidRunStateError(
                    "async operation transition is unavailable"
                )
            row = connection.execute(
                "SELECT * FROM async_operations WHERE id = ?",
                (operation_id,),
            ).fetchone()
        return _operation_from_row(row)


def _operation_from_row(row) -> AsyncOperation:
    result = (
        json.loads(row["result_json"])
        if row["result_json"] is not None
        else None
    )
    return AsyncOperation(
        id=str(row["id"]),
        request_id=(
            str(row["request_id"])
            if row["request_id"] is not None
            else None
        ),
        operation_id=str(row["operation_id"]),
        scope=str(row["scope"]),
        request_hash=str(row["request_hash"]),
        status=str(row["status"]),
        result=result,
        error_code=(
            str(row["error_code"])
            if row["error_code"] is not None
            else None
        ),
        created_at=int(row["created_at"]),
        started_at=(
            int(row["started_at"])
            if row["started_at"] is not None
            else None
        ),
        completed_at=(
            int(row["completed_at"])
            if row["completed_at"] is not None
            else None
        ),
    )
