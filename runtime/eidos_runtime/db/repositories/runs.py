from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import uuid

from eidos_runtime.db.database import CommittedMutation, Repository, now_ms as _now_ms
from eidos_runtime.db.errors import (
    ActiveRunError,
    InvalidRunStateError,
    ResourceNotFoundError,
    StorageError,
    WorkspaceBoundaryError,
)
from eidos_runtime.db.events import append_event, event_from_row
from eidos_runtime.db.mappers import (
    _bounded_canonical_json,
    _item_from_row,
    _run_from_row,
)
from eidos_runtime.db.transitions import (
    settle_run_children,
    transition_run,
    transition_segments,
)
from eidos_runtime.model.client import ModelProfileSnapshot
from eidos_runtime.model.config import (
    DEFAULT_MODEL_ID,
    SUPPORTED_MODELS,
    default_profile_snapshot,
)
from eidos_runtime.runtime.state_machine import (
    EventType,
    RunStatus,
    SegmentStatus,
    ensure_transition,
)

EMPTY_EXTENSION_SNAPSHOT = {
    "schemaVersion": 1,
    "extensionContractVersion": 1,
    "plugins": [],
    "skillCatalogHash": "0" * 64,
    "mcpConfigHash": "0" * 64,
}


def _finalization_attempt(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": row["id"],
        "runId": row["run_id"],
        "stepId": row["step_id"],
        "status": row["status"],
        "startedAt": row["started_at"],
        "completedAt": row["completed_at"],
        "errorCode": row["error_code"],
        "errorMessage": row["error_message"],
        "modelId": row["model_id"],
        "outputItemId": row["output_item_id"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


class RunRepository(Repository):
    def create_run(
        self,
        session_id: str,
        user_input: str,
        *,
        operation_id: str | None = None,
        queued: bool = False,
        session_title: str | None = None,
        model_id: str = DEFAULT_MODEL_ID,
        model_profile: ModelProfileSnapshot | None = None,
        extension_snapshot: dict[str, object] | None = None,
    ) -> tuple[dict[str, object], dict[str, object]]:
        if session_title is not None and (
            not session_title
            or len(session_title) > 60
            or len(session_title.encode("utf-8")) > 120
        ):
            raise ValueError("session title is invalid")
        if model_id not in SUPPORTED_MODELS:
            raise ValueError("model is unsupported")
        profile = model_profile or default_profile_snapshot(model_id)
        if profile.provider_id != "deepseek" or profile.model_id != model_id:
            raise ValueError("model profile does not match run")
        model_profile_json = profile.model_dump_json()
        extension_snapshot_json = _bounded_canonical_json(
            extension_snapshot or EMPTY_EXTENSION_SNAPSHOT,
            code="extension_snapshot_invalid",
        )
        run_id = str(uuid.uuid4())
        item_id = str(uuid.uuid4())
        now = _now_ms()
        def write(
            connection: sqlite3.Connection,
        ) -> dict[str, object]:
            session = connection.execute(
                "SELECT id, workspace_root, title FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if session is None:
                raise ResourceNotFoundError("session not found")
            if self._workspace_overlaps_data(Path(session["workspace_root"])):
                raise WorkspaceBoundaryError("workspace overlaps runtime data")
            if session["title"] is None and session_title is not None:
                connection.execute(
                    "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                    (session_title, now, session_id),
                )
                append_event(
                    connection,
                    EventType.SESSION_TITLE_UPDATED,
                    now,
                    {"title": session_title},
                    session_id=session_id,
                )
            status = "queued" if queued else "running"
            started_at = None if queued else now
            try:
                connection.execute(
                    """
                    INSERT INTO runs (
                        id, session_id, user_input, model_id, model_profile_json,
                        status, enqueued_at,
                        extension_snapshot_json, created_at, started_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id, session_id, user_input, model_id, model_profile_json,
                        status,
                        now if queued else None, extension_snapshot_json,
                        now, started_at, now,
                    ),
                )
            except sqlite3.IntegrityError as error:
                if "one_active_run" in str(error) or "UNIQUE constraint failed" in str(error):
                    raise ActiveRunError("another run is active") from None
                raise
            connection.execute(
                """
                INSERT INTO items (
                    id, session_id, run_id, ordinal, kind, status,
                    content, created_at, completed_at
                ) VALUES (?, ?, ?, 1, 'user_message', 'completed', ?, ?, ?)
                """,
                (item_id, session_id, run_id, user_input, now, now),
            )
            connection.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            run = _run_from_row(connection.execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone())
            append_event(
                connection,
                EventType.RUN_CREATED,
                now,
                {"run": run},
                session_id=session_id,
                run_id=run_id,
            )
            item_row = connection.execute(
                "SELECT * FROM items WHERE id = ?", (item_id,)
            ).fetchone()
            return {"run": run, "item": _item_from_row(item_row, None)}
        result = self._write(
            write,
            operation_id=operation_id,
            operation_scope="run/start",
            operation_request={
                "sessionId": session_id,
                "userInput": user_input,
                "modelId": model_id,
                "extensionSnapshot": json.loads(extension_snapshot_json),
            },
        )
        return result["run"], result["item"]

    def enqueue_run(
        self,
        session_id: str,
        user_input: str,
        *,
        operation_id: str | None = None,
        session_title: str | None = None,
        model_id: str = DEFAULT_MODEL_ID,
        model_profile: ModelProfileSnapshot | None = None,
        extension_snapshot: dict[str, object] | None = None,
    ) -> tuple[dict[str, object], dict[str, object]]:
        return self.create_run(
            session_id,
            user_input,
            operation_id=operation_id,
            queued=True,
            session_title=session_title,
            model_id=model_id,
            model_profile=model_profile,
            extension_snapshot=extension_snapshot,
        )

    def claim_next_run(self) -> dict[str, object] | None:
        mutation = self.claim_next_run_committed()
        return mutation.value if mutation is not None else None

    def claim_next_run_committed(
        self,
    ) -> CommittedMutation[dict[str, object]] | None:
        segment_id = str(uuid.uuid4())
        with self.lock, self._connection() as connection:
            now = _now_ms()
            row = connection.execute(
                """
                SELECT id FROM runs
                WHERE status = 'queued'
                  AND cancel_requested_at IS NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM runs WHERE status IN ('running', 'finalizing')
                  )
                ORDER BY enqueued_at ASC, creation_seq ASC LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            existing_segment = connection.execute(
                """
                SELECT id, status FROM execution_segments
                WHERE run_id = ? AND status IN ('queued', 'running')
                ORDER BY ordinal DESC LIMIT 1
                """,
                (row["id"],),
            ).fetchone()
            if existing_segment is None:
                ordinal = connection.execute(
                    "SELECT COUNT(*) + 1 FROM execution_segments WHERE run_id = ?",
                    (row["id"],),
                ).fetchone()[0]
                connection.execute(
                    """
                    INSERT INTO execution_segments (
                        id, run_id, ordinal, status, created_at, started_at
                    ) VALUES (?, ?, ?, 'running', ?, ?)
                    """,
                    (segment_id, row["id"], ordinal, now, now),
                )
            elif existing_segment["status"] == SegmentStatus.QUEUED.value:
                ensure_transition(SegmentStatus.QUEUED, SegmentStatus.RUNNING)
                connection.execute(
                    """
                    UPDATE execution_segments SET status = 'running',
                        started_at = COALESCE(started_at, ?)
                    WHERE id = ?
                    """,
                    (now, existing_segment["id"]),
                )
            run, event = transition_run(
                connection,
                str(row["id"]),
                frozenset({RunStatus.QUEUED}),
                RunStatus.RUNNING,
                "fifo_claim",
            )
        return CommittedMutation(run, (event,))

    def read_run(self, run_id: str) -> dict[str, object]:
        with self.lock:
            row = self._connection().execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise ResourceNotFoundError("run not found")
        return _run_from_row(row)

    def read_model_profile(self, run_id: str) -> ModelProfileSnapshot:
        with self.lock:
            row = self._connection().execute(
                "SELECT model_profile_json FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise ResourceNotFoundError("run not found")
        try:
            return ModelProfileSnapshot.model_validate_json(row["model_profile_json"])
        except (TypeError, ValueError):
            raise StorageError("model_profile_invalid") from None

    def run_budget(self, run_id: str) -> dict[str, int]:
        with self.lock:
            connection = self._connection()
            run = connection.execute(
                "SELECT model_step_count, total_effective_ms FROM runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            segment = connection.execute(
                """
                SELECT step_count, effective_ms FROM execution_segments
                WHERE run_id = ? AND status = 'running'
                ORDER BY ordinal DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        if run is None:
            raise ResourceNotFoundError("run not found")
        return {
            "segmentStepsRemaining": max(0, 20 - int(segment["step_count"] if segment else 0)),
            "runStepsRemaining": max(0, 80 - int(run["model_step_count"])),
            "segmentEffectiveMsRemaining": max(
                0, 1_800_000 - int(segment["effective_ms"] if segment else 0)
            ),
            "runEffectiveMsRemaining": max(0, 7_200_000 - int(run["total_effective_ms"])),
        }

    def read_runtime_start_event(self, run_id: str) -> dict[str, object]:
        with self.lock:
            row = self._connection().execute(
                """
                SELECT * FROM events
                WHERE run_id = ? AND event_type IN ('run.created', 'run.status_changed')
                ORDER BY id DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        event = event_from_row(row) if row is not None else None
        if event is None:
            raise ResourceNotFoundError("run start event not found")
        return event

    def clear_rejects(self, run_id: str) -> None:
        with self.lock, self._connection() as connection:
            connection.execute(
                "UPDATE runs SET consecutive_rejects = 0 WHERE id = ?",
                (run_id,),
            )

    def approval_prompt_blocked(self, run_id: str) -> bool:
        with self.lock:
            row = self._connection().execute(
                "SELECT consecutive_rejects FROM runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise ResourceNotFoundError("run not found")
        return int(row["consecutive_rejects"]) > 0

    def record_sensitive_tool_input(self, run_id: str) -> int:
        with self.lock, self._connection() as connection:
            connection.execute(
                """
                UPDATE runs SET consecutive_sensitive_tool_inputs =
                    consecutive_sensitive_tool_inputs + 1
                WHERE id = ? AND status = 'running'
                """,
                (run_id,),
            )
            row = connection.execute(
                "SELECT consecutive_sensitive_tool_inputs FROM runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise ResourceNotFoundError("run not found")
        return int(row["consecutive_sensitive_tool_inputs"])

    def clear_sensitive_tool_inputs(self, run_id: str) -> None:
        with self.lock, self._connection() as connection:
            connection.execute(
                "UPDATE runs SET consecutive_sensitive_tool_inputs = 0 WHERE id = ?",
                (run_id,),
            )

    def side_effects_blocked(self, run_id: str) -> bool:
        with self.lock:
            row = self._connection().execute(
                "SELECT reconciliation_required FROM runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise ResourceNotFoundError("run not found")
        return bool(row["reconciliation_required"])

    def record_protocol_error(self, run_id: str) -> int:
        with self.lock, self._connection() as connection:
            updated = connection.execute(
                """
                UPDATE runs
                SET consecutive_protocol_errors = consecutive_protocol_errors + 1,
                    updated_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (_now_ms(), run_id),
            )
            if updated.rowcount != 1:
                raise InvalidRunStateError("run is not active")
            row = connection.execute(
                "SELECT consecutive_protocol_errors FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
        return row["consecutive_protocol_errors"]

    def clear_protocol_errors(self, run_id: str) -> None:
        with self.lock, self._connection() as connection:
            connection.execute(
                """
                UPDATE runs SET consecutive_protocol_errors = 0, updated_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (_now_ms(), run_id),
            )

    def begin_finalization(self, run_id: str) -> dict[str, object]:
        return self.begin_finalization_committed(run_id).value

    def begin_finalization_committed(
        self, run_id: str
    ) -> CommittedMutation[dict[str, object]]:
        with self.lock, self._connection() as connection:
            run, event = transition_run(
                connection,
                run_id,
                frozenset({RunStatus.RUNNING}),
                RunStatus.FINALIZING,
                "run_limit",
            )
        return CommittedMutation(run, (event,))

    def begin_finalization_attempt_committed(
        self, run_id: str, *, model_id: str
    ) -> CommittedMutation[tuple[dict[str, object], dict[str, object]]]:
        attempt_id = str(uuid.uuid4())
        with self.lock, self._connection() as connection:
            run_row = connection.execute(
                "SELECT session_id, status FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if run_row is None:
                raise ResourceNotFoundError("run not found")
            if run_row["status"] != RunStatus.RUNNING.value:
                raise InvalidRunStateError("run status changed")
            now = _now_ms()
            connection.execute(
                """
                INSERT INTO finalization_attempts (
                    id, run_id, status, started_at, model_id, created_at, updated_at
                ) VALUES (?, ?, 'running', ?, ?, ?, ?)
                """,
                (attempt_id, run_id, now, model_id, now, now),
            )
            attempt_event = append_event(
                connection,
                EventType.FINALIZATION_STATUS_CHANGED,
                now,
                {
                    "entity_id": attempt_id,
                    "previous": "created",
                    "current": "running",
                },
                session_id=run_row["session_id"],
                run_id=run_id,
            )
            run, run_event = transition_run(
                connection,
                run_id,
                frozenset({RunStatus.RUNNING}),
                RunStatus.FINALIZING,
                "run_limit",
            )
            attempt = _finalization_attempt(connection.execute(
                "SELECT * FROM finalization_attempts WHERE id = ?",
                (attempt_id,),
            ).fetchone())
        return CommittedMutation((attempt, run), (attempt_event, run_event))

    def read_finalization_attempts(
        self, run_id: str
    ) -> tuple[dict[str, object], ...]:
        with self.lock:
            rows = self._connection().execute(
                """
                SELECT * FROM finalization_attempts
                WHERE run_id = ? ORDER BY creation_seq
                """,
                (run_id,),
            ).fetchall()
        return tuple(_finalization_attempt(row) for row in rows)

    def stop_run(self, run_id: str, reason: str) -> dict[str, object]:
        return self.stop_run_committed(run_id, reason).value

    def stop_run_committed(
        self, run_id: str, reason: str
    ) -> CommittedMutation[dict[str, object]]:
        with self.lock, self._connection() as connection:
            now = _now_ms()
            segment_events = transition_segments(
                connection,
                run_id,
                frozenset({SegmentStatus.RUNNING}),
                SegmentStatus.COMPLETED,
                now,
                "run_stopped",
            )
            run, event = transition_run(
                connection,
                run_id,
                frozenset({RunStatus.FINALIZING}),
                RunStatus.STOPPED,
                reason,
            )
        return CommittedMutation(run, (*segment_events, event))

    def complete_finalization_and_stop_committed(
        self,
        item_id: str | None,
        run_id: str,
        stop_reason: str,
        *,
        attempt_id: str | None = None,
        attempt_status: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> CommittedMutation[
        tuple[dict[str, object] | None, dict[str, object]]
    ]:
        with self.lock, self._connection() as connection:
            run_row = connection.execute(
                "SELECT session_id, status FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if run_row is None:
                raise ResourceNotFoundError("run not found")
            if run_row["status"] != RunStatus.FINALIZING.value:
                raise InvalidRunStateError("run status changed")
            item: dict[str, object] | None = None
            item_event: dict[str, object] | None = None
            attempt_event: dict[str, object] | None = None
            now = _now_ms()
            if attempt_id is not None:
                if attempt_status not in {
                    "completed",
                    "timed_out",
                    "model_failed",
                    "sensitive_rejected",
                }:
                    raise ValueError("invalid finalization attempt status")
                updated = connection.execute(
                    """
                    UPDATE finalization_attempts
                    SET status = ?, completed_at = ?, error_code = ?,
                        error_message = ?, output_item_id = ?, updated_at = ?
                    WHERE id = ? AND run_id = ? AND status = 'running'
                    """,
                    (
                        attempt_status,
                        now,
                        error_code,
                        error_message,
                        item_id,
                        now,
                        attempt_id,
                        run_id,
                    ),
                )
                if updated.rowcount != 1:
                    raise InvalidRunStateError(
                        "finalization attempt is not active"
                    )
                attempt_event = append_event(
                    connection,
                    EventType.FINALIZATION_STATUS_CHANGED,
                    now,
                    {
                        "entity_id": attempt_id,
                        "previous": "running",
                        "current": attempt_status,
                        "reason": error_code,
                    },
                    session_id=run_row["session_id"],
                    run_id=run_id,
                )
            if item_id is not None:
                item_row = connection.execute(
                    """
                    SELECT * FROM items
                    WHERE id = ? AND run_id = ? AND kind = 'assistant_message'
                      AND model_step_index IS NULL AND status = 'in_progress'
                    """,
                    (item_id, run_id),
                ).fetchone()
                if item_row is None:
                    raise InvalidRunStateError("finalization item is not active")
                updated = connection.execute(
                    """
                    UPDATE items SET status = 'completed', completed_at = ?
                    WHERE id = ? AND run_id = ? AND status = 'in_progress'
                    """,
                    (now, item_id, run_id),
                )
                if updated.rowcount != 1:
                    raise InvalidRunStateError("finalization item is not active")
                item_event = append_event(
                    connection,
                    EventType.ITEM_COMPLETED,
                    now,
                    {"item_id": item_id},
                    session_id=run_row["session_id"],
                    run_id=run_id,
                )
                item = _item_from_row(
                    connection.execute(
                        "SELECT * FROM items WHERE id = ?", (item_id,)
                    ).fetchone(),
                    None,
                )
            segment_events = transition_segments(
                connection,
                run_id,
                frozenset({SegmentStatus.RUNNING}),
                SegmentStatus.COMPLETED,
                now,
                "run_stopped",
            )
            run, run_event = transition_run(
                connection,
                run_id,
                frozenset({RunStatus.FINALIZING}),
                RunStatus.STOPPED,
                stop_reason,
            )
        events = (
            *((attempt_event,) if attempt_event is not None else ()),
            *((item_event,) if item_event is not None else ()),
            *segment_events,
            run_event,
        )
        return CommittedMutation((item, run), events)

    def fail_run(self, run_id: str, error_code: str) -> dict[str, object]:
        return self.fail_run_committed(run_id, error_code).value

    def fail_run_committed(
        self, run_id: str, error_code: str
    ) -> CommittedMutation[dict[str, object]]:
        with self.lock, self._connection() as connection:
            now = _now_ms()
            events = list(settle_run_children(
                connection, run_id, RunStatus.FAILED, now
            ))
            run, event = transition_run(
                connection,
                run_id,
                frozenset({
                    RunStatus.RUNNING,
                    RunStatus.WAITING_APPROVAL,
                    RunStatus.FINALIZING,
                }),
                RunStatus.FAILED,
                error_code,
            )
            events.append(event)
        return CommittedMutation(run, tuple(events))

    def cancel_run(
        self, run_id: str, *, operation_id: str | None = None
    ) -> dict[str, object]:
        def write(connection: sqlite3.Connection) -> dict[str, object]:
            return self._cancel_run_transaction(connection, run_id).value

        return self._write(
            write,
            operation_id=operation_id,
            operation_scope="run/cancel" if operation_id is not None else None,
            operation_request={"runId": run_id} if operation_id is not None else None,
        )

    def request_cancel_committed(
        self, run_id: str
    ) -> CommittedMutation[dict[str, object]]:
        with self.lock, self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise ResourceNotFoundError("run not found")
            if row["status"] not in {
                "queued",
                "running",
                "waiting_approval",
                "finalizing",
                "canceled",
            }:
                raise InvalidRunStateError("run cannot be canceled")
            if row["status"] == "canceled":
                return CommittedMutation(_run_from_row(row), ())
            now = _now_ms()
            connection.execute(
                """
                UPDATE runs
                SET cancel_requested_at = COALESCE(cancel_requested_at, ?),
                    cancel_failure_code = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, now, run_id),
            )
            run = _run_from_row(connection.execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone())
            event = append_event(
                connection,
                EventType.RUN_UPDATED,
                now,
                {"reason": "cancel_requested"},
                session_id=str(run["sessionId"]),
                run_id=run_id,
            )
        return CommittedMutation(run, (event,))

    def mark_cancel_failed_committed(
        self, run_id: str, failure_code: str
    ) -> CommittedMutation[dict[str, object]]:
        with self.lock, self._connection() as connection:
            row = connection.execute(
                "SELECT session_id FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise ResourceNotFoundError("run not found")
            now = _now_ms()
            changed = connection.execute(
                """
                UPDATE runs
                SET cancel_failure_code = ?, updated_at = ?
                WHERE id = ? AND cancel_requested_at IS NOT NULL
                  AND cancel_completed_at IS NULL
                """,
                (failure_code, now, run_id),
            )
            if changed.rowcount != 1:
                raise InvalidRunStateError("cancel is not pending")
            run = _run_from_row(connection.execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone())
            event = append_event(
                connection,
                EventType.RUN_UPDATED,
                now,
                {"reason": "cancel_failed"},
                session_id=row["session_id"],
                run_id=run_id,
            )
        return CommittedMutation(run, (event,))

    def complete_requested_cancel_committed(
        self, run_id: str
    ) -> CommittedMutation[dict[str, object]]:
        with self.lock, self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise ResourceNotFoundError("run not found")
            if row["status"] == RunStatus.CANCELED.value:
                return CommittedMutation(_run_from_row(row), ())
            if row["cancel_requested_at"] is None:
                raise InvalidRunStateError("cancel was not requested")
            current = RunStatus(row["status"])
            expected = frozenset({
                RunStatus.QUEUED,
                RunStatus.RUNNING,
                RunStatus.WAITING_APPROVAL,
                RunStatus.FINALIZING,
            })
            if current not in expected:
                raise InvalidRunStateError("run cannot finish cancellation")
            now = _now_ms()
            if row["reconciliation_required"] or row["side_effects_may_exist"]:
                events = list(settle_run_children(
                    connection, run_id, RunStatus.INTERRUPTED, now
                ))
                run, event = transition_run(
                    connection,
                    run_id,
                    frozenset({current}),
                    RunStatus.INTERRUPTED,
                    "side_effect_reconciliation_required",
                )
                events.append(event)
                connection.execute(
                    """
                    UPDATE runs
                    SET cancel_failure_code = 'RECONCILIATION_REQUIRED'
                    WHERE id = ?
                    """,
                    (run_id,),
                )
                run = _run_from_row(connection.execute(
                    "SELECT * FROM runs WHERE id = ?", (run_id,)
                ).fetchone())
                return CommittedMutation(run, tuple(events))
            events = list(settle_run_children(
                connection, run_id, RunStatus.CANCELED, now
            ))
            run, event = transition_run(
                connection,
                run_id,
                frozenset({current}),
                RunStatus.CANCELED,
                "user_cancel",
            )
            events.append(event)
        return CommittedMutation(run, tuple(events))

    def nonterminal_run_ids(self) -> tuple[str, ...]:
        with self.lock:
            rows = self._connection().execute(
                """
                SELECT id FROM runs
                WHERE status IN (
                    'queued', 'running', 'waiting_approval', 'finalizing'
                )
                ORDER BY creation_seq
                """
            ).fetchall()
        return tuple(str(row["id"]) for row in rows)

    def cancel_run_committed(
        self, run_id: str
    ) -> CommittedMutation[dict[str, object]]:
        with self.lock, self._connection() as connection:
            return self._cancel_run_transaction(connection, run_id)

    def cancel_waiting_approval_committed(
        self, run_id: str
    ) -> CommittedMutation[dict[str, object]]:
        with self.lock, self._connection() as connection:
            return self._cancel_run_transaction(
                connection,
                run_id,
                expected=frozenset({RunStatus.WAITING_APPROVAL}),
            )

    def _cancel_run_transaction(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        *,
        expected: frozenset[RunStatus] | None = None,
    ) -> CommittedMutation[dict[str, object]]:
        row = connection.execute(
            "SELECT status FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise ResourceNotFoundError("run not found")
        if row["status"] == RunStatus.CANCELED.value:
            run = _run_from_row(connection.execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone())
            return CommittedMutation(run, ())
        expected = expected or frozenset({
            RunStatus.QUEUED,
            RunStatus.RUNNING,
            RunStatus.WAITING_APPROVAL,
            RunStatus.FINALIZING,
        })
        current = RunStatus(row["status"])
        if current not in expected:
            raise InvalidRunStateError("run cannot be canceled")
        now = _now_ms()
        connection.execute(
            """
            UPDATE runs
            SET cancel_requested_at = COALESCE(cancel_requested_at, ?)
            WHERE id = ?
            """,
            (now, run_id),
        )
        events = list(settle_run_children(
            connection, run_id, RunStatus.CANCELED, now
        ))
        run, event = transition_run(
            connection, run_id, expected, RunStatus.CANCELED, "user_cancel"
        )
        events.append(event)
        return CommittedMutation(run, tuple(events))

    def interrupt_run(self, run_id: str) -> dict[str, object]:
        return self.interrupt_run_committed(run_id).value

    def interrupt_run_committed(
        self, run_id: str
    ) -> CommittedMutation[dict[str, object]]:
        with self.lock, self._connection() as connection:
            row = connection.execute(
                "SELECT status FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise ResourceNotFoundError("run not found")
            if row["status"] == "interrupted":
                return CommittedMutation(self.read_run(run_id), ())
            if row["status"] not in {"running", "waiting_approval"}:
                raise InvalidRunStateError("run cannot be interrupted")
            now = _now_ms()
            events = list(settle_run_children(
                connection, run_id, RunStatus.INTERRUPTED, now
            ))
            run, event = transition_run(
                connection,
                run_id,
                frozenset({RunStatus.RUNNING, RunStatus.WAITING_APPROVAL}),
                RunStatus.INTERRUPTED,
                "runtime_interrupted",
            )
            events.append(event)
        return CommittedMutation(run, tuple(events))

    def canceled_items_for_run(self, run_id: str) -> list[dict[str, object]]:
        with self.lock:
            connection = self._connection()
            rows = connection.execute(
                """
                SELECT * FROM items
                WHERE run_id = ? AND status = 'canceled'
                ORDER BY ordinal ASC
                """,
                (run_id,),
            ).fetchall()
            tool_rows = connection.execute(
                """
                SELECT tool_calls.* FROM tool_calls
                JOIN items ON items.id = tool_calls.item_id
                WHERE items.run_id = ? AND items.status = 'canceled'
                """,
                (run_id,),
            ).fetchall()
        tools_by_item = {row["item_id"]: row for row in tool_rows}
        return [
            _item_from_row(row, tools_by_item.get(row["id"])) for row in rows
        ]
