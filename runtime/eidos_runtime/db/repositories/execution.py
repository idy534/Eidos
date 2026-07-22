from __future__ import annotations

import hashlib
import json
from pathlib import Path
import uuid

from eidos_runtime.db.database import (
    CommittedMutation,
    Repository,
    WorkspaceIdentity,
    canonical_hash as _canonical_hash,
    now_ms as _now_ms,
)
from eidos_runtime.db.errors import (
    InvalidRunStateError,
    ResourceNotFoundError,
    RunLimitReached,
    SegmentLimitReached,
    StorageError,
)
from eidos_runtime.db.events import append_event
from eidos_runtime.db.mappers import (
    _bounded_canonical_json,
    _item_from_row,
    _load_json_object,
    _model_attempt_from_row,
    _run_from_row,
)
from eidos_runtime.db.transitions import transition_run, transition_segments
from eidos_runtime.model.client import ModelUsage
from eidos_runtime.runtime.contracts import ProgressSignature
from eidos_runtime.runtime.state_machine import (
    ApprovalStatus,
    EventType,
    RunStatus,
    SegmentStatus,
    StepStatus,
    ToolCallStatus,
    ensure_transition,
)

class ExecutionRepository(Repository):
    def read_item(self, item_id: str) -> dict[str, object]:
        with self.lock:
            row = self._connection().execute(
                "SELECT * FROM items WHERE id = ?", (item_id,)
            ).fetchone()
            if row is None:
                raise ResourceNotFoundError("item not found")
            tool_row = self._connection().execute(
                "SELECT * FROM tool_calls WHERE item_id = ?", (item_id,)
            ).fetchone()
        return _item_from_row(row, tool_row)

    def get_user_item(self, run_id: str) -> dict[str, object]:
        with self.lock:
            row = self._connection().execute(
                """
                SELECT * FROM items
                WHERE run_id = ? AND kind = 'user_message'
                ORDER BY ordinal ASC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            raise ResourceNotFoundError("user item not found")
        return _item_from_row(row, None)

    def workspace_for_run(self, run_id: str) -> WorkspaceIdentity:
        with self.lock:
            row = self._connection().execute(
                """
                SELECT sessions.workspace_root, sessions.workspace_dev,
                       sessions.workspace_inode, sessions.workspace_uid
                FROM sessions
                JOIN runs ON runs.session_id = sessions.id
                WHERE runs.id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            raise ResourceNotFoundError("run not found")
        if any(
            row[field] is None
            for field in ("workspace_dev", "workspace_inode", "workspace_uid")
        ):
            raise StorageError("workspace identity is unavailable")
        return WorkspaceIdentity(
            path=Path(row["workspace_root"]),
            device=row["workspace_dev"],
            inode=row["workspace_inode"],
            owner=row["workspace_uid"],
        )

    def increment_model_step(
        self,
        run_id: str,
        *,
        tool_snapshot: dict[str, object] | None = None,
    ) -> int:
        tool_snapshot_json = (
            _bounded_canonical_json(tool_snapshot, code="tool_snapshot_invalid")
            if tool_snapshot is not None else None
        )
        tool_set_hash = tool_snapshot.get("toolSetHash") if tool_snapshot else None
        if tool_set_hash is not None and (
            not isinstance(tool_set_hash, str) or len(tool_set_hash) != 64
        ):
            raise ValueError("tool_snapshot_invalid")
        with self.lock, self._connection() as connection:
            run = connection.execute(
                "SELECT * FROM runs WHERE id = ? AND status = 'running'", (run_id,)
            ).fetchone()
            if run is None:
                raise InvalidRunStateError("run is not active")
            if run["model_step_count"] >= 80:
                raise RunLimitReached("run step limit reached")
            if run["total_effective_ms"] >= 7_200_000:
                raise RunLimitReached("run time limit reached")
            segment = connection.execute(
                """
                SELECT * FROM execution_segments
                WHERE run_id = ? AND status = 'running'
                ORDER BY ordinal DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            if segment is None:
                segment_id = str(uuid.uuid4())
                ordinal = connection.execute(
                    "SELECT COUNT(*) + 1 FROM execution_segments WHERE run_id = ?",
                    (run_id,),
                ).fetchone()[0]
                now = _now_ms()
                connection.execute(
                    """
                    INSERT INTO execution_segments (
                        id, run_id, ordinal, status, created_at, started_at
                    ) VALUES (?, ?, ?, 'running', ?, ?)
                    """,
                    (segment_id, run_id, ordinal, now, now),
                )
                segment = connection.execute(
                    "SELECT * FROM execution_segments WHERE id = ?", (segment_id,)
                ).fetchone()
            if segment["step_count"] >= 20:
                raise SegmentLimitReached("segment step limit reached")
            if segment["effective_ms"] >= 1_800_000:
                raise SegmentLimitReached("segment time limit reached")
            now = _now_ms()
            step_id = str(uuid.uuid4())
            attempt_id = str(uuid.uuid4())
            step_ordinal = segment["step_count"] + 1
            connection.execute(
                """
                INSERT INTO steps (
                    id, run_id, segment_id, ordinal, status,
                    observed_reconciliation_epoch, tool_snapshot_json,
                    tool_set_hash, created_at
                ) VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?)
                """,
                (
                    step_id, run_id, segment["id"], step_ordinal,
                    run["reconciliation_epoch"], tool_snapshot_json,
                    tool_set_hash, now,
                ),
            )
            connection.execute(
                """
                INSERT INTO model_attempts (
                    id, step_id, ordinal, status, started_at
                ) VALUES (?, ?, 1, 'running', ?)
                """,
                (attempt_id, step_id, now),
            )
            updated = connection.execute(
                """
                UPDATE runs
                SET model_step_count = model_step_count + 1, updated_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (_now_ms(), run_id),
            )
            if updated.rowcount != 1:
                raise InvalidRunStateError("run is not active")
            connection.execute(
                "UPDATE execution_segments SET step_count = step_count + 1 WHERE id = ?",
                (segment["id"],),
            )
            row = connection.execute(
                "SELECT model_step_count FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
        return row["model_step_count"]

    def read_step_tool_snapshot(
        self, run_id: str, model_step_index: int
    ) -> dict[str, object]:
        with self.lock:
            row = self._connection().execute(
                """
                SELECT tool_snapshot_json FROM steps
                WHERE run_id = ? ORDER BY creation_seq LIMIT 1 OFFSET ?
                """,
                (run_id, model_step_index - 1),
            ).fetchone()
        if row is None or row["tool_snapshot_json"] is None:
            raise ResourceNotFoundError("tool snapshot not found")
        value = json.loads(row["tool_snapshot_json"])
        if not isinstance(value, dict):
            raise StorageError("tool_snapshot_invalid")
        return value

    def read_current_step_fact(self, run_id: str) -> dict[str, object]:
        with self.lock:
            row = self._connection().execute(
                """
                SELECT id, observed_reconciliation_epoch
                FROM steps
                WHERE run_id = ? AND status = 'running'
                ORDER BY creation_seq DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            raise ResourceNotFoundError("current step not found")
        return {
            "stepId": row["id"],
            "reconciliationEpoch": row["observed_reconciliation_epoch"],
        }

    def add_effective_time(self, run_id: str, elapsed_ms: int) -> None:
        self.add_effective_time_committed(run_id, elapsed_ms)

    def add_effective_time_committed(
        self, run_id: str, elapsed_ms: int
    ) -> CommittedMutation[dict[str, object]] | None:
        if elapsed_ms <= 0:
            return None
        with self.lock, self._connection() as connection:
            connection.execute(
                "UPDATE runs SET total_effective_ms = total_effective_ms + ? WHERE id = ?",
                (elapsed_ms, run_id),
            )
            connection.execute(
                """
                UPDATE execution_segments
                SET effective_ms = effective_ms + ?
                WHERE id = (
                    SELECT id FROM execution_segments
                    WHERE run_id = ? AND status = 'running'
                    ORDER BY ordinal DESC LIMIT 1
                )
                """,
                (elapsed_ms, run_id),
            )
            run = _run_from_row(connection.execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone())
            event = append_event(
                connection,
                EventType.RUN_UPDATED,
                _now_ms(),
                {"reason": "effective_time"},
                session_id=str(run["sessionId"]),
                run_id=run_id,
            )
        return CommittedMutation(run, (event,))

    def complete_current_step(
        self,
        run_id: str,
        status_value: str,
        *,
        reason: str | None = None,
        progress_signature: ProgressSignature | None = None,
    ) -> None:
        if status_value not in {"completed", "failed", "canceled"}:
            raise ValueError("invalid step status")
        target_status = StepStatus(status_value)
        now = _now_ms()
        with self.lock, self._connection() as connection:
            step = connection.execute(
                """
                SELECT * FROM steps
                WHERE run_id = ? AND status = 'running'
                ORDER BY creation_seq DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            if step is None:
                return
            ensure_transition(StepStatus.RUNNING, target_status)
            connection.execute(
                """
                UPDATE model_attempts
                SET status = ?, completed_at = ?,
                    error_code = COALESCE(error_code, ?)
                WHERE step_id = ? AND status = 'running'
                """,
                (status_value, now, reason, step["id"]),
            )
            connection.execute(
                """
                UPDATE steps
                SET status = ?, completed_at = ?, progress_signature_json = ?
                WHERE id = ? AND status = 'running'
                """,
                (
                    status_value,
                    now,
                    progress_signature.model_dump_json()
                    if progress_signature is not None else None,
                    step["id"],
                ),
            )
            run = connection.execute(
                "SELECT session_id FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            append_event(
                connection, EventType.STEP_STATUS_CHANGED, now,
                {
                    "entity_id": step["id"], "previous": "running",
                    "current": status_value, "reason": reason,
                },
                session_id=run["session_id"], run_id=run_id,
            )
            if status_value == "completed":
                run_state = connection.execute(
                    "SELECT * FROM runs WHERE id = ?", (run_id,)
                ).fetchone()
                if (
                    run_state["reconciliation_required"]
                    and step["observed_reconciliation_epoch"]
                    == run_state["reconciliation_epoch"]
                ):
                    observations = connection.execute(
                        """
                        SELECT tool_calls.result_json
                        FROM tool_calls JOIN items ON items.id = tool_calls.item_id
                        WHERE items.run_id = ?
                          AND items.model_step_index = ?
                          AND tool_calls.tool_name IN (
                            'list_files', 'read_file', 'read_file_range', 'search_text'
                          )
                          AND tool_calls.status = 'completed'
                        """,
                        (run_id, run_state["model_step_count"]),
                    ).fetchall()
                    observed = any(
                        isinstance(result, dict) and result.get("outcome") == "success"
                        for row in observations
                        for result in [_load_json_object(row["result_json"])]
                    )

                    if observed:
                        cleared = connection.execute(
                            """
                            UPDATE runs SET reconciliation_required = 0, updated_at = ?
                            WHERE id = ? AND reconciliation_required = 1
                              AND reconciliation_epoch = ?
                            """,
                            (now, run_id, step["observed_reconciliation_epoch"]),
                        )
                        if cleared.rowcount == 1:
                            append_event(
                                connection, EventType.RECONCILIATION_CLEARED, now,
                                {
                                    "epoch": step["observed_reconciliation_epoch"],
                                    "reason": "read_only_observation",
                                },
                                session_id=run["session_id"], run_id=run_id,
                            )

    def recent_progress_signatures(
        self, run_id: str, limit: int = 8
    ) -> tuple[ProgressSignature, ...]:
        if not 1 <= limit <= 32:
            raise ValueError("invalid signature history limit")
        with self.lock:
            rows = self._connection().execute(
                """
                SELECT progress_signature_json FROM steps
                WHERE run_id = ? AND progress_signature_json IS NOT NULL
                ORDER BY creation_seq DESC LIMIT ?
                """,
                (run_id, limit),
            ).fetchall()
        return tuple(
            ProgressSignature.model_validate_json(row[0]) for row in reversed(rows)
        )

    def complete_current_model_attempt(
        self,
        run_id: str,
        status: str,
        *,
        usage: ModelUsage | None = None,
        provider_name: str | None = None,
        resolved_model_name: str | None = None,
        finish_reason: str | None = None,
        provider_response_id: str | None = None,
        error_code: str | None = None,
        http_status: int | None = None,
        ttft_ms: int | None = None,
        duration_ms: int | None = None,
        had_progress: bool = False,
    ) -> bool:
        if status not in {"completed", "failed", "canceled"}:
            raise ValueError("invalid model attempt status")
        now = _now_ms()
        with self.lock, self._connection() as connection:
            attempt = connection.execute(
                """
                SELECT model_attempts.id FROM model_attempts
                JOIN steps ON steps.id = model_attempts.step_id
                WHERE steps.run_id = ? AND model_attempts.status = 'running'
                ORDER BY model_attempts.creation_seq DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            if attempt is None:
                return False
            changed = connection.execute(
                """
                UPDATE model_attempts
                SET status = ?, completed_at = ?, provider_name = ?,
                    resolved_model_name = ?, finish_reason = ?,
                    provider_response_id = ?, usage_json = ?, error_code = ?,
                    http_status = ?, ttft_ms = ?, duration_ms = ?, had_progress = ?
                WHERE id = ? AND status = 'running'
                """,
                (
                    status, now, provider_name, resolved_model_name, finish_reason,
                    provider_response_id,
                    usage.model_dump_json() if usage is not None else None,
                    error_code, http_status, ttft_ms, duration_ms,
                    int(had_progress), attempt["id"],
                ),
            )
        return changed.rowcount == 1

    def start_retry_model_attempt(self, run_id: str) -> None:
        """Create the next Attempt immediately before its provider request."""
        now = _now_ms()
        with self.lock, self._connection() as connection:
            step = connection.execute(
                """
                SELECT steps.id FROM steps JOIN runs ON runs.id = steps.run_id
                WHERE steps.run_id = ? AND steps.status = 'running'
                  AND runs.status = 'running'
                ORDER BY steps.creation_seq DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            if step is None:
                raise InvalidRunStateError("model attempt cannot retry")
            running_attempt = connection.execute(
                """
                SELECT id FROM model_attempts
                WHERE step_id = ? AND status = 'running'
                LIMIT 1
                """,
                (step["id"],),
            ).fetchone()
            if running_attempt is not None:
                raise InvalidRunStateError("model attempt cannot retry")
            last = connection.execute(
                """
                SELECT COALESCE(MAX(ordinal), 0) AS ordinal FROM model_attempts
                WHERE step_id = ?
                """,
                (step["id"],),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO model_attempts (
                    id, step_id, ordinal, status, started_at
                ) VALUES (?, ?, ?, 'running', ?)
                """,
                (str(uuid.uuid4()), step["id"], int(last["ordinal"]) + 1, now),
            )

    def read_model_attempts(self, run_id: str) -> list[dict[str, object]]:
        with self.lock:
            rows = self._connection().execute(
                """
                SELECT model_attempts.* FROM model_attempts
                JOIN steps ON steps.id = model_attempts.step_id
                WHERE steps.run_id = ?
                ORDER BY model_attempts.creation_seq
                """,
                (run_id,),
            ).fetchall()
        return [_model_attempt_from_row(row) for row in rows]

    def create_assistant_item(
        self, run_id: str, model_step_index: int
    ) -> dict[str, object]:
        return self.create_assistant_item_committed(run_id, model_step_index).value

    def create_assistant_item_committed(
        self, run_id: str, model_step_index: int
    ) -> CommittedMutation[dict[str, object]]:
        return self._create_item_committed(run_id, "assistant_message", model_step_index)

    def create_finalization_assistant_item(
        self, run_id: str
    ) -> dict[str, object]:
        """Create an explicitly step-less Item while the Run is finalizing."""
        return self.create_finalization_assistant_item_committed(run_id).value

    def create_finalization_assistant_item_committed(
        self, run_id: str
    ) -> CommittedMutation[dict[str, object]]:
        return self._create_item_committed(run_id, "assistant_message", None)

    def append_item_content(self, item_id: str, delta: str) -> dict[str, object]:
        with self.lock, self._connection() as connection:
            updated = connection.execute(
                """
                UPDATE items SET content = COALESCE(content, '') || ?
                WHERE id = ? AND status = 'in_progress'
                """,
                (delta, item_id),
            )
            if updated.rowcount != 1:
                raise InvalidRunStateError("item is not active")
        return self.read_item(item_id)

    def append_item_deltas_committed(
        self,
        item_id: str,
        deltas: tuple[str, ...],
        first_sequence: int,
    ) -> CommittedMutation[dict[str, object]]:
        if not deltas:
            raise ValueError("at least one delta is required")
        with self.lock, self._connection() as connection:
            fact = connection.execute(
                "SELECT session_id, run_id FROM items WHERE id = ? AND status = 'in_progress'",
                (item_id,),
            ).fetchone()
            if fact is None:
                raise InvalidRunStateError("item is not active")
            updated = connection.execute(
                """
                UPDATE items SET content = COALESCE(content, '') || ?
                WHERE id = ? AND status = 'in_progress'
                """,
                ("".join(deltas), item_id),
            )
            if updated.rowcount != 1:
                raise InvalidRunStateError("item is not active")
            now = _now_ms()
            events = tuple(
                append_event(
                    connection,
                    EventType.ITEM_DELTA,
                    now,
                    {
                        "item_id": item_id,
                        "sequence": first_sequence + offset,
                        "delta": delta,
                    },
                    session_id=fact["session_id"],
                    run_id=fact["run_id"],
                )
                for offset, delta in enumerate(deltas)
            )
        item = self.read_item(item_id)
        return CommittedMutation(item, events)

    def complete_assistant_item(self, item_id: str) -> dict[str, object]:
        return self.complete_assistant_item_committed(item_id).value

    def complete_assistant_item_committed(
        self, item_id: str
    ) -> CommittedMutation[dict[str, object]]:
        return self._complete_item_committed(item_id, "completed")

    def mark_assistant_incomplete(self, item_id: str) -> dict[str, object]:
        return self.mark_assistant_incomplete_committed(item_id).value

    def mark_assistant_incomplete_committed(
        self, item_id: str
    ) -> CommittedMutation[dict[str, object]]:
        mutation = self.mark_assistant_incomplete_if_active_committed(item_id)
        if mutation is None:
            raise InvalidRunStateError("assistant item is not active")
        return mutation

    def mark_assistant_incomplete_if_active_committed(
        self, item_id: str
    ) -> CommittedMutation[dict[str, object]] | None:
        with self.lock, self._connection() as connection:
            now = _now_ms()
            fact = connection.execute(
                "SELECT session_id, run_id FROM items WHERE id = ?",
                (item_id,),
            ).fetchone()
            if fact is None:
                raise ResourceNotFoundError("item not found")
            updated = connection.execute(
                """
                UPDATE items
                SET status = 'failed', incomplete = 1, completed_at = ?
                WHERE id = ? AND kind = 'assistant_message'
                  AND status = 'in_progress'
                """,
                (now, item_id),
            )
            if updated.rowcount != 1:
                return None
            event = append_event(
                connection,
                EventType.ITEM_COMPLETED,
                now,
                {"item_id": item_id},
                session_id=fact["session_id"],
                run_id=fact["run_id"],
            )
        return CommittedMutation(self.read_item(item_id), (event,))

    def complete_assistant_and_run(
        self, item_id: str, run_id: str
    ) -> tuple[dict[str, object], dict[str, object]]:
        return self.complete_assistant_and_run_committed(item_id, run_id).value

    def complete_assistant_and_run_committed(
        self, item_id: str, run_id: str
    ) -> CommittedMutation[tuple[dict[str, object], dict[str, object]]]:
        with self.lock, self._connection() as connection:
            now = _now_ms()
            item_update = connection.execute(
                """
                UPDATE items SET status = 'completed', completed_at = ?
                WHERE id = ? AND run_id = ? AND status = 'in_progress'
                """,
                (now, item_id, run_id),
            )
            if item_update.rowcount != 1:
                raise InvalidRunStateError("assistant item is not active")
            segment_events = transition_segments(
                connection,
                run_id,
                frozenset({SegmentStatus.RUNNING}),
                SegmentStatus.COMPLETED,
                now,
                "run_succeeded",
            )
            item_event = append_event(
                connection,
                EventType.ITEM_COMPLETED,
                now,
                {"item_id": item_id},
                session_id=connection.execute(
                    "SELECT session_id FROM runs WHERE id = ?", (run_id,)
                ).fetchone()["session_id"],
                run_id=run_id,
            )
            run, run_event = transition_run(
                connection,
                run_id,
                frozenset({RunStatus.RUNNING}),
                RunStatus.SUCCEEDED,
                None,
            )
        item = self.read_item(item_id)
        return CommittedMutation(
            (item, run), (*segment_events, item_event, run_event)
        )

    def create_tool_item(
        self,
        run_id: str,
        model_step_index: int,
        batch_order: int,
        provider_call_id: str,
        tool_name: str,
        arguments_json: str,
        *,
        provenance: dict[str, object] | None = None,
        tool_set_hash: str | None = None,
    ) -> dict[str, object]:
        return self.create_tool_item_committed(
            run_id,
            model_step_index,
            batch_order,
            provider_call_id,
            tool_name,
            arguments_json,
            provenance=provenance,
            tool_set_hash=tool_set_hash,
        ).value

    def create_tool_item_committed(
        self,
        run_id: str,
        model_step_index: int,
        batch_order: int,
        provider_call_id: str,
        tool_name: str,
        arguments_json: str,
        *,
        provenance: dict[str, object] | None = None,
        tool_set_hash: str | None = None,
    ) -> CommittedMutation[dict[str, object]]:
        provenance_json = (
            _bounded_canonical_json(provenance, code="tool_provenance_invalid")
            if provenance is not None else None
        )
        if tool_set_hash is not None and len(tool_set_hash) != 64:
            raise ValueError("tool_set_hash_invalid")
        item_id = str(uuid.uuid4())
        tool_call_id = str(uuid.uuid4())
        now = _now_ms()
        item_kind = (
            "file_change"
            if tool_name in {"write_file", "apply_patch"}
            else "command_execution"
            if tool_name == "run_shell"
            else "tool_call"
        )
        with self.lock, self._connection() as connection:
            run = connection.execute(
                "SELECT session_id FROM runs WHERE id = ? AND status = 'running'",
                (run_id,),
            ).fetchone()
            if run is None:
                raise InvalidRunStateError("run is not active")
            ordinal = self._next_ordinal(connection, run_id)
            connection.execute(
                """
                INSERT INTO items (
                    id, session_id, run_id, ordinal, model_step_index,
                    kind, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'in_progress', ?)
                """,
                (
                    item_id,
                    run["session_id"],
                    run_id,
                    ordinal,
                    model_step_index,
                    item_kind,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO tool_calls (
                    id, item_id, model_step_index, batch_order, provider_call_id,
                    tool_name, status, arguments_json, provenance_json,
                    tool_set_hash, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?)
                """,
                (
                    tool_call_id,
                    item_id,
                    model_step_index,
                    batch_order,
                    provider_call_id,
                    tool_name,
                    arguments_json,
                    provenance_json,
                    tool_set_hash,
                    now,
                ),
            )
            tool_event = append_event(
                connection, EventType.TOOL_CALL_STARTED, now,
                {"tool_call_id": tool_call_id},
                session_id=run["session_id"], run_id=run_id,
            )
            item_event = append_event(
                connection,
                EventType.ITEM_STARTED,
                now,
                {"item_id": item_id},
                session_id=run["session_id"],
                run_id=run_id,
            )
        return CommittedMutation(
            self.read_item(item_id), (tool_event, item_event)
        )

    def complete_tool_item(
        self,
        item_id: str,
        result_json: str,
        *,
        item_status: str = "completed",
        tool_status: str = "completed",
        workspace_changed: bool = False,
        diff_hash: str | None = None,
    ) -> dict[str, object]:
        return self.complete_tool_item_committed(
            item_id,
            result_json,
            item_status=item_status,
            tool_status=tool_status,
            workspace_changed=workspace_changed,
            diff_hash=diff_hash,
        ).value

    def complete_tool_item_committed(
        self,
        item_id: str,
        result_json: str,
        *,
        item_status: str = "completed",
        tool_status: str = "completed",
        workspace_changed: bool = False,
        diff_hash: str | None = None,
    ) -> CommittedMutation[dict[str, object]]:
        if item_status not in {"completed", "failed", "declined", "canceled"}:
            raise ValueError("invalid item status")
        if tool_status not in {"completed", "failed", "canceled"}:
            raise ValueError("invalid tool status")
        if diff_hash is not None and (
            len(diff_hash) != 64 or any(value not in "0123456789abcdef" for value in diff_hash)
        ):
            raise ValueError("invalid diff hash")
        now = _now_ms()
        events: list[dict[str, object]] = []
        with self.lock, self._connection() as connection:
            fact = connection.execute(
                """
                SELECT tool_calls.id AS tool_call_id, tool_calls.tool_name,
                       tool_calls.status AS tool_status,
                       items.run_id, items.session_id
                FROM tool_calls JOIN items ON items.id = tool_calls.item_id
                WHERE items.id = ?
                """,
                (item_id,),
            ).fetchone()
            if fact is None:
                raise InvalidRunStateError("tool item is unavailable")
            ensure_transition(
                ToolCallStatus(fact["tool_status"]), ToolCallStatus(tool_status)
            )
            tool_update = connection.execute(
                """
                UPDATE tool_calls
                SET status = ?, result_json = ?, completed_at = ?
                WHERE item_id = ? AND status = 'running'
                """,
                (tool_status, result_json, now, item_id),
            )
            item_update = connection.execute(
                """
                UPDATE items SET status = ?, completed_at = ?
                WHERE id = ? AND status = 'in_progress'
                """,
                (item_status, now, item_id),
            )
            if tool_update.rowcount != 1 or item_update.rowcount != 1:
                raise InvalidRunStateError("tool item is not active")
            if workspace_changed:
                connection.execute(
                    """
                    UPDATE runs SET workspace_version = workspace_version + 1,
                                    last_diff_hash = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (diff_hash, now, fact["run_id"]),
                )
            try:
                result = json.loads(result_json)
            except json.JSONDecodeError:
                result = {}
            reconciliation_codes = {
                "file_commit_uncertain", "outcome_unknown", "nonzero_exit",
                "shell_exit_nonzero", "timeout", "tool_timeout", "interrupted",
                "background_process", "output_capture_failed",
                "workspace_change_manifest_incomplete", "shell_resource_limit_exceeded",
            }
            reconciliation_required = (
                result.get("reconciliationRequired") is True
                or result.get("code") in reconciliation_codes
            )
            intent_status = "uncertain" if reconciliation_required else "completed"
            connection.execute(
                """
                UPDATE durable_intents SET status = ?, reconciled_at = ?
                WHERE tool_call_id = ? AND status = 'running'
                """,
                (intent_status, now, fact["tool_call_id"]),
            )
            events.append(append_event(
                connection, EventType.TOOL_CALL_COMPLETED, now,
                {"tool_call_id": fact["tool_call_id"], "code": result.get("code")},
                session_id=fact["session_id"], run_id=fact["run_id"],
            ))
            events.append(append_event(
                connection,
                EventType.ITEM_COMPLETED,
                now,
                {"item_id": item_id},
                session_id=fact["session_id"],
                run_id=fact["run_id"],
            ))
            if reconciliation_required:
                connection.execute(
                    """
                    UPDATE runs
                    SET reconciliation_required = 1,
                        reconciliation_epoch = reconciliation_epoch + 1,
                        side_effects_may_exist = 1,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now, fact["run_id"]),
                )
                epoch = connection.execute(
                    "SELECT reconciliation_epoch FROM runs WHERE id = ?",
                    (fact["run_id"],),
                ).fetchone()[0]
                events.append(append_event(
                    connection, EventType.RECONCILIATION_REQUIRED, now,
                    {"epoch": epoch, "reason": str(result.get("code", "outcome_unknown"))},
                    session_id=fact["session_id"], run_id=fact["run_id"],
                ))
        return CommittedMutation(self.read_item(item_id), tuple(events))

    def begin_approval(
        self,
        item_id: str,
        diff: str,
        base_sha256: str | None,
    ) -> dict[str, object]:
        return self.begin_approval_committed(item_id, diff, base_sha256).value

    def begin_approval_committed(
        self,
        item_id: str,
        diff: str,
        base_sha256: str | None,
    ) -> CommittedMutation[dict[str, object]]:
        now = _now_ms()
        approval_id = str(uuid.uuid4())
        with self.lock, self._connection() as connection:
            row = connection.execute(
                """
                SELECT items.run_id, tool_calls.id AS tool_call_id,
                       tool_calls.arguments_json
                FROM items JOIN tool_calls ON tool_calls.item_id = items.id
                WHERE items.id = ? AND items.status = 'in_progress'
                """,
                (item_id,),
            ).fetchone()
            if row is None:
                raise InvalidRunStateError("tool item is not active")
            tool_update = connection.execute(
                """
                UPDATE tool_calls
                SET approval_status = 'pending', approval_diff = ?, base_sha256 = ?
                WHERE item_id = ? AND status = 'running'
                """,
                (diff, base_sha256, item_id),
            )
            if tool_update.rowcount != 1:
                raise InvalidRunStateError("approval cannot start")
            connection.execute(
                """
                INSERT INTO approvals (
                    id, tool_call_id, run_id, item_id, status,
                    request_hash, created_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    approval_id, row["tool_call_id"], row["run_id"], item_id,
                    _canonical_hash({
                        "argumentsJson": row["arguments_json"],
                        "diff": diff,
                        "baseSha256": base_sha256,
                    }),
                    now,
                ),
            )
            _run, run_event = transition_run(
                connection,
                str(row["run_id"]),
                frozenset({RunStatus.RUNNING}),
                RunStatus.WAITING_APPROVAL,
                "approval_required",
            )
            approval_event = append_event(
                connection, EventType.APPROVAL_STATUS_CHANGED, now,
                {
                    "entity_id": approval_id, "previous": "created",
                    "current": "pending",
                },
                run_id=row["run_id"],
            )
        return CommittedMutation(
            self.read_item(item_id), (run_event, approval_event)
        )

    def resolve_approval(
        self,
        item_id: str,
        decision: str,
        feedback: str | None,
        *,
        requeue: bool = False,
    ) -> dict[str, object]:
        return self.resolve_approval_committed(
            item_id, decision, feedback, requeue=requeue
        ).value

    def resolve_approval_committed(
        self,
        item_id: str,
        decision: str,
        feedback: str | None,
        *,
        requeue: bool = False,
    ) -> CommittedMutation[dict[str, object]]:
        if decision not in {"approve", "reject"}:
            raise ValueError("invalid approval decision")
        now = _now_ms()
        with self.lock, self._connection() as connection:
            row = connection.execute(
                "SELECT run_id FROM items WHERE id = ? AND status = 'in_progress'",
                (item_id,),
            ).fetchone()
            if row is None:
                raise InvalidRunStateError("tool item is not active")
            tool_update = connection.execute(
                """
                UPDATE tool_calls
                SET approval_status = 'resolved', approval_decision = ?,
                    approval_feedback = ?
                WHERE item_id = ? AND status = 'running'
                  AND approval_status = 'pending'
                """,
                (decision, feedback, item_id),
            )
            approval = connection.execute(
                "SELECT id FROM approvals WHERE item_id = ? AND status = 'pending'",
                (item_id,),
            ).fetchone()
            if approval is None:
                raise InvalidRunStateError("approval is no longer pending")
            next_status = "approved" if decision == "approve" else "rejected"
            ensure_transition(
                ApprovalStatus.PENDING,
                ApprovalStatus(next_status),
            )
            approval_update = connection.execute(
                """
                UPDATE approvals
                SET status = ?, decision = ?, feedback = ?, decided_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (next_status, decision, feedback, now, approval["id"]),
            )
            run_state = connection.execute(
                "SELECT consecutive_rejects FROM runs WHERE id = ?",
                (row["run_id"],),
            ).fetchone()
            rejects = run_state["consecutive_rejects"] + (1 if decision == "reject" else 0)
            run_status = (
                "waiting_user_input" if rejects >= 2
                else "queued" if requeue else "running"
            )
            run_update = connection.execute(
                """
                UPDATE runs
                SET consecutive_rejects = ?
                WHERE id = ? AND status = 'waiting_approval'
                """,
                (rejects, row["run_id"]),
            )
            if (
                tool_update.rowcount != 1
                or approval_update.rowcount != 1
                or run_update.rowcount != 1
            ):
                raise InvalidRunStateError("approval is no longer pending")
            target = RunStatus(run_status)
            reason = (
                "repeated_approval_rejection"
                if target is RunStatus.WAITING_USER_INPUT
                else "approval_resolved"
            )
            _run, run_event = transition_run(
                connection,
                str(row["run_id"]),
                frozenset({RunStatus.WAITING_APPROVAL}),
                target,
                reason,
            )
            approval_event = append_event(
                connection, EventType.APPROVAL_STATUS_CHANGED, now,
                {
                    "entity_id": approval["id"], "previous": "pending",
                    "current": next_status,
                },
                run_id=row["run_id"],
            )
        return CommittedMutation(
            self.read_item(item_id), (approval_event, run_event)
        )

    def begin_durable_intent(
        self,
        item_id: str,
        *,
        preconditions: dict[str, object],
    ) -> str:
        intent_id = str(uuid.uuid4())
        nonce = str(uuid.uuid4())
        now = _now_ms()
        with self.lock, self._connection() as connection:
            row = connection.execute(
                """
                SELECT items.run_id, items.session_id, tool_calls.id AS tool_call_id,
                       tool_calls.arguments_json
                FROM items JOIN tool_calls ON tool_calls.item_id = items.id
                JOIN approvals ON approvals.item_id = items.id
                WHERE items.id = ? AND items.status = 'in_progress'
                  AND tool_calls.status = 'running'
                  AND approvals.status = 'approved'
                """,
                (item_id,),
            ).fetchone()
            if row is None:
                raise InvalidRunStateError("approved intent is unavailable")
            connection.execute(
                """
                INSERT INTO durable_intents (
                    id, run_id, tool_call_id, execution_nonce,
                    arguments_hash, preconditions_json, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?)
                """,
                (
                    intent_id, row["run_id"], row["tool_call_id"], nonce,
                    hashlib.sha256(row["arguments_json"].encode("utf-8")).hexdigest(),
                    json.dumps(
                        preconditions,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    now,
                ),
            )
            append_event(
                connection, EventType.TOOL_CALL_STARTED, now,
                {"tool_call_id": row["tool_call_id"]},
                session_id=row["session_id"], run_id=row["run_id"],
            )
        return intent_id

    def has_read_evidence(
        self, run_id: str, path: str, sha256: str
    ) -> bool:
        with self.lock:
            rows = self._connection().execute(
                """
                SELECT tool_calls.arguments_json, tool_calls.result_json
                FROM tool_calls
                JOIN items ON items.id = tool_calls.item_id
                WHERE items.run_id = ? AND items.status = 'completed'
                  AND tool_calls.tool_name = 'read_file'
                  AND tool_calls.status = 'completed'
                ORDER BY tool_calls.creation_seq DESC
                """,
                (run_id,),
            ).fetchall()
        for row in rows:
            try:
                arguments = json.loads(row["arguments_json"])
                result = json.loads(row["result_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            if (
                arguments == {"path": path}
                and result.get("outcome") == "success"
                and result.get("data", {}).get("sha256") == sha256
            ):
                return True
        return False

    def enqueue_input(self, run_id: str, content: str) -> str:
        if not content or len(content.encode("utf-8")) > 64 * 1024:
            raise ValueError("input is invalid")
        input_id = str(uuid.uuid4())
        now = _now_ms()
        with self.lock, self._connection() as connection:
            run = connection.execute(
                "SELECT session_id FROM runs WHERE id = ? AND status = 'running'",
                (run_id,),
            ).fetchone()
            if run is None:
                raise InvalidRunStateError("run is not active")
            connection.execute(
                """
                INSERT INTO input_mailbox (id, run_id, content, status, created_at)
                VALUES (?, ?, ?, 'pending', ?)
                """,
                (input_id, run_id, content, now),
            )
            append_event(
                connection, EventType.INPUT_QUEUED, now, {"inputId": input_id},
                session_id=run["session_id"], run_id=run_id,
            )
        return input_id

    def has_pending_input(self, run_id: str) -> bool:
        with self.lock:
            row = self._connection().execute(
                "SELECT 1 FROM input_mailbox WHERE run_id = ? AND status = 'pending' LIMIT 1",
                (run_id,),
            ).fetchone()
        return row is not None

    def consume_pending_inputs(self, run_id: str) -> int:
        return len(self.consume_pending_input_facts(run_id))

    def consume_pending_input_facts(
        self, run_id: str
    ) -> tuple[tuple[str, str], ...]:
        now = _now_ms()
        injected: list[tuple[str, str]] = []
        with self.lock, self._connection() as connection:
            run = connection.execute(
                "SELECT session_id FROM runs WHERE id = ? AND status = 'running'",
                (run_id,),
            ).fetchone()
            if run is None:
                raise InvalidRunStateError("run is not active")
            pending = connection.execute(
                """
                SELECT * FROM input_mailbox
                WHERE run_id = ? AND status = 'pending' ORDER BY creation_seq
                """,
                (run_id,),
            ).fetchall()
            for entry in pending:
                item_id = str(uuid.uuid4())
                connection.execute(
                    """
                    INSERT INTO items (
                        id, session_id, run_id, ordinal, kind, status,
                        content, created_at, completed_at
                    ) VALUES (?, ?, ?, ?, 'user_message', 'completed', ?, ?, ?)
                    """,
                    (
                        item_id, run["session_id"], run_id,
                        self._next_ordinal(connection, run_id), entry["content"], now, now,
                    ),
                )
                updated = connection.execute(
                    """
                    UPDATE input_mailbox SET status = 'injected', injected_at = ?
                    WHERE id = ? AND status = 'pending'
                    """,
                    (now, entry["id"]),
                )
                if updated.rowcount != 1:
                    raise InvalidRunStateError("pending input changed")
                append_event(
                    connection, EventType.INPUT_INJECTED, now,
                    {"inputId": entry["id"]},
                    session_id=run["session_id"], run_id=run_id,
                )
                injected.append((item_id, str(entry["content"])))
        return tuple(injected)

    def _create_item(
        self, run_id: str, kind: str, model_step_index: int | None
    ) -> dict[str, object]:
        return self._create_item_committed(run_id, kind, model_step_index).value

    def _create_item_committed(
        self, run_id: str, kind: str, model_step_index: int | None
    ) -> CommittedMutation[dict[str, object]]:
        item_id = str(uuid.uuid4())
        now = _now_ms()
        with self.lock, self._connection() as connection:
            run = connection.execute(
                "SELECT session_id FROM runs WHERE id = ? AND status IN ('running', 'finalizing')",
                (run_id,),
            ).fetchone()
            if run is None:
                raise InvalidRunStateError("run is not active")
            ordinal = self._next_ordinal(connection, run_id)
            connection.execute(
                """
                INSERT INTO items (
                    id, session_id, run_id, ordinal, model_step_index,
                    kind, status, content, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'in_progress', '', ?)
                """,
                (
                    item_id,
                    run["session_id"],
                    run_id,
                    ordinal,
                    model_step_index,
                    kind,
                    now,
                ),
            )
            event = append_event(
                connection,
                EventType.ITEM_STARTED,
                now,
                {"item_id": item_id},
                session_id=run["session_id"],
                run_id=run_id,
            )
        return CommittedMutation(self.read_item(item_id), (event,))

    def _complete_item(self, item_id: str, status_value: str) -> dict[str, object]:
        return self._complete_item_committed(item_id, status_value).value

    def _complete_item_committed(
        self, item_id: str, status_value: str
    ) -> CommittedMutation[dict[str, object]]:
        with self.lock, self._connection() as connection:
            fact = connection.execute(
                "SELECT session_id, run_id FROM items WHERE id = ?",
                (item_id,),
            ).fetchone()
            if fact is None:
                raise ResourceNotFoundError("item not found")
            now = _now_ms()
            updated = connection.execute(
                """
                UPDATE items SET status = ?, completed_at = ?
                WHERE id = ? AND status = 'in_progress'
                """,
                (status_value, now, item_id),
            )
            if updated.rowcount != 1:
                raise InvalidRunStateError("item is not active")
            event = append_event(
                connection,
                EventType.ITEM_COMPLETED,
                now,
                {"item_id": item_id},
                session_id=fact["session_id"],
                run_id=fact["run_id"],
            )
        return CommittedMutation(self.read_item(item_id), (event,))
