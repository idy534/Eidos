from __future__ import annotations

from dataclasses import dataclass
import sqlite3

from eidos_runtime.db.database import now_ms as _now_ms
from eidos_runtime.db.errors import InvalidRunStateError, ResourceNotFoundError
from eidos_runtime.db.events import append_event
from eidos_runtime.db.mappers import _run_from_row
from eidos_runtime.runtime.state_machine import (
    ApprovalStatus,
    EventType,
    RunStatus,
    SegmentStatus,
    StepStatus,
    ToolCallStatus,
    ensure_transition,
)


@dataclass(frozen=True)
class ApprovalResolution:
    run: dict[str, object]
    approval_id: str
    approval_status: ApprovalStatus
    events: tuple[dict[str, object], ...]


def transition_run(
    connection: sqlite3.Connection,
    run_id: str,
    expected_statuses: frozenset[RunStatus],
    target_status: RunStatus,
    reason: str | None,
) -> tuple[dict[str, object], dict[str, object]]:
    """Validate, update, and event one persisted Run transition."""
    row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise ResourceNotFoundError("run not found")
    current = RunStatus(row["status"])
    if current not in expected_statuses:
        raise InvalidRunStateError("run status changed")
    ensure_transition(current, target_status)
    now = _now_ms()
    updates: dict[str, object] = {"status": target_status.value, "updated_at": now}
    if target_status is RunStatus.RUNNING:
        updates.update({"started_at": row["started_at"] or now, "enqueued_at": None})
    elif target_status is RunStatus.QUEUED:
        updates["enqueued_at"] = now
    elif target_status is RunStatus.WAITING_USER_INPUT:
        updates["pause_reason"] = reason
    elif target_status is RunStatus.FAILED:
        updates.update({"error_code": reason, "completed_at": now})
    elif target_status is RunStatus.STOPPED:
        updates.update({"stop_reason": reason, "completed_at": now})
    elif target_status is RunStatus.CANCELED:
        updates.update({
            "completed_at": now,
            "cancel_completed_at": now,
            "cancel_failure_code": None,
        })
    elif target_status is RunStatus.INTERRUPTED:
        updates.update({"error_code": "RUNTIME_INTERRUPTED", "completed_at": now})
    elif target_status is RunStatus.SUCCEEDED:
        updates["completed_at"] = now
    assignments = ", ".join(f"{column} = ?" for column in updates)
    changed = connection.execute(
        f"UPDATE runs SET {assignments} WHERE id = ? AND status = ?",
        (*updates.values(), run_id, current.value),
    )
    if changed.rowcount != 1:
        raise InvalidRunStateError("run status changed")
    run = _run_from_row(
        connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    )
    event = append_event(
        connection,
        EventType.RUN_STATUS_CHANGED,
        now,
        {"previous": current, "current": target_status, "reason": reason},
        session_id=str(run["sessionId"]),
        run_id=run_id,
    )
    return run, event

def transition_segments(
    connection: sqlite3.Connection,
    run_id: str,
    expected_statuses: frozenset[SegmentStatus],
    target_status: SegmentStatus,
    now: int,
    reason: str,
) -> tuple[dict[str, object], ...]:
    run = connection.execute(
        "SELECT session_id FROM runs WHERE id = ?", (run_id,)
    ).fetchone()
    if run is None:
        raise ResourceNotFoundError("run not found")
    placeholders = ",".join("?" for _ in expected_statuses)
    rows = connection.execute(
        f"""
        SELECT id, status FROM execution_segments
        WHERE run_id = ? AND status IN ({placeholders})
        """,
        (run_id, *(status.value for status in expected_statuses)),
    ).fetchall()
    events: list[dict[str, object]] = []
    for row in rows:
        current = SegmentStatus(row["status"])
        ensure_transition(current, target_status)
        completed_at = (
            now
            if target_status in {
                SegmentStatus.COMPLETED,
                SegmentStatus.FAILED,
                SegmentStatus.CANCELED,
            }
            else None
        )
        connection.execute(
            """
            UPDATE execution_segments
            SET status = ?, completed_at = ?,
                started_at = CASE
                    WHEN ? = 'running' THEN COALESCE(started_at, ?)
                    ELSE started_at
                END
            WHERE id = ? AND status = ?
            """,
            (
                target_status.value,
                completed_at,
                target_status.value,
                now,
                row["id"],
                current.value,
            ),
        )
        events.append(append_event(
            connection,
            EventType.SEGMENT_STATUS_CHANGED,
            now,
            {
                "entity_id": row["id"],
                "previous": current.value,
                "current": target_status.value,
                "reason": reason,
            },
            session_id=run["session_id"],
            run_id=run_id,
        ))
    return tuple(events)


def resolve_approval_and_transition(
    connection: sqlite3.Connection,
    *,
    item_id: str,
    decision: str,
    feedback: str | None,
    requeue: bool,
) -> ApprovalResolution:
    if decision not in {"approve", "reject"}:
        raise ValueError("invalid approval decision")
    fact = connection.execute(
        """
        SELECT items.run_id, runs.consecutive_rejects, approvals.id AS approval_id
        FROM items
        JOIN runs ON runs.id = items.run_id
        JOIN approvals ON approvals.item_id = items.id
        WHERE items.id = ? AND items.status = 'in_progress'
          AND runs.status = 'waiting_approval'
          AND approvals.status = 'pending'
        """,
        (item_id,),
    ).fetchone()
    if fact is None:
        raise InvalidRunStateError("approval is no longer pending")

    now = _now_ms()
    target_approval = (
        ApprovalStatus.APPROVED
        if decision == "approve"
        else ApprovalStatus.REJECTED
    )
    ensure_transition(ApprovalStatus.PENDING, target_approval)
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
    approval_update = connection.execute(
        """
        UPDATE approvals
        SET status = ?, decision = ?, feedback = ?, decided_at = ?
        WHERE id = ? AND status = 'pending'
        """,
        (
            target_approval.value,
            decision,
            feedback,
            now,
            fact["approval_id"],
        ),
    )
    rejects = int(fact["consecutive_rejects"]) + (
        1 if decision == "reject" else 0
    )
    reject_update = connection.execute(
        """
        UPDATE runs SET consecutive_rejects = ?
        WHERE id = ? AND status = 'waiting_approval'
        """,
        (rejects, fact["run_id"]),
    )
    if (
        tool_update.rowcount != 1
        or approval_update.rowcount != 1
        or reject_update.rowcount != 1
    ):
        raise InvalidRunStateError("approval is no longer pending")

    target_run = RunStatus.QUEUED if requeue else RunStatus.RUNNING
    reason = "approval_resolved"
    events: list[dict[str, object]] = []
    if target_run is RunStatus.QUEUED:
        events.extend(transition_segments(
            connection,
            str(fact["run_id"]),
            frozenset({SegmentStatus.RUNNING}),
            SegmentStatus.QUEUED,
            now,
            reason,
        ))
    run, run_event = transition_run(
        connection,
        str(fact["run_id"]),
        frozenset({RunStatus.WAITING_APPROVAL}),
        target_run,
        reason,
    )
    approval_event = append_event(
        connection,
        EventType.APPROVAL_STATUS_CHANGED,
        now,
        {
            "entity_id": fact["approval_id"],
            "previous": ApprovalStatus.PENDING.value,
            "current": target_approval.value,
        },
        session_id=str(run["sessionId"]),
        run_id=str(fact["run_id"]),
    )
    return ApprovalResolution(
        run=run,
        approval_id=str(fact["approval_id"]),
        approval_status=target_approval,
        events=(*events, run_event, approval_event),
    )

def settle_run_children(
    connection: sqlite3.Connection,
    run_id: str,
    target_status: RunStatus,
    now: int,
) -> tuple[dict[str, object], ...]:
    """Settle active child facts in the same transaction as a terminal Run."""
    run = connection.execute(
        "SELECT session_id FROM runs WHERE id = ?", (run_id,)
    ).fetchone()
    if run is None:
        raise ResourceNotFoundError("run not found")
    events: list[dict[str, object]] = []
    tool_rows = connection.execute(
        """
        SELECT tool_calls.id FROM tool_calls
        JOIN items ON items.id = tool_calls.item_id
        WHERE items.run_id = ? AND tool_calls.status = 'running'
        """,
        (run_id,),
    ).fetchall()
    for tool in tool_rows:
        ensure_transition(ToolCallStatus.RUNNING, ToolCallStatus.CANCELED)
        connection.execute(
            """
            UPDATE tool_calls SET status = 'canceled', completed_at = ?
            WHERE id = ? AND status = 'running'
            """,
            (now, tool["id"]),
        )
        events.append(append_event(
            connection,
            EventType.TOOL_CALL_COMPLETED,
            now,
            {"tool_call_id": tool["id"], "code": "run_terminated"},
            session_id=run["session_id"],
            run_id=run_id,
        ))

    approval_target = (
        ApprovalStatus.CANCELED
        if target_status is RunStatus.CANCELED
        else ApprovalStatus.INVALIDATED
    )
    approvals = connection.execute(
        "SELECT id FROM approvals WHERE run_id = ? AND status = 'pending'",
        (run_id,),
    ).fetchall()
    for approval in approvals:
        ensure_transition(ApprovalStatus.PENDING, approval_target)
        connection.execute(
            "UPDATE approvals SET status = ?, decided_at = ? WHERE id = ? AND status = 'pending'",
            (approval_target.value, now, approval["id"]),
        )
        events.append(append_event(
            connection,
            EventType.APPROVAL_STATUS_CHANGED,
            now,
            {
                "entity_id": approval["id"],
                "previous": ApprovalStatus.PENDING.value,
                "current": approval_target.value,
            },
            session_id=run["session_id"],
            run_id=run_id,
        ))
    connection.execute(
        """
        UPDATE tool_calls SET approval_status = 'canceled'
        WHERE approval_status = 'pending'
          AND item_id IN (SELECT id FROM items WHERE run_id = ?)
        """,
        (run_id,),
    )

    finalization_target = (
        "canceled" if target_status is RunStatus.CANCELED else "interrupted"
    )
    attempts = connection.execute(
        """
        SELECT id FROM finalization_attempts
        WHERE run_id = ? AND status = 'running'
        """,
        (run_id,),
    ).fetchall()
    for attempt in attempts:
        error_code = (
            "finalization_canceled"
            if finalization_target == "canceled"
            else "finalization_interrupted"
        )
        connection.execute(
            """
            UPDATE finalization_attempts
            SET status = ?, completed_at = ?, error_code = ?, updated_at = ?
            WHERE id = ? AND status = 'running'
            """,
            (
                finalization_target,
                now,
                error_code,
                now,
                attempt["id"],
            ),
        )
        events.append(append_event(
            connection,
            EventType.FINALIZATION_STATUS_CHANGED,
            now,
            {
                "entity_id": attempt["id"],
                "previous": "running",
                "current": finalization_target,
                "reason": error_code,
            },
            session_id=run["session_id"],
            run_id=run_id,
        ))

    item_rows = connection.execute(
        "SELECT id FROM items WHERE run_id = ? AND status = 'in_progress'",
        (run_id,),
    ).fetchall()
    for item in item_rows:
        connection.execute(
            """
            UPDATE items SET status = 'canceled', completed_at = ?
            WHERE id = ? AND status = 'in_progress'
            """,
            (now, item["id"]),
        )
        events.append(append_event(
            connection,
            EventType.ITEM_COMPLETED,
            now,
            {"item_id": item["id"]},
            session_id=run["session_id"],
            run_id=run_id,
        ))

    step_target = (
        StepStatus.CANCELED
        if target_status is RunStatus.CANCELED
        else StepStatus.FAILED
    )
    steps = connection.execute(
        "SELECT id FROM steps WHERE run_id = ? AND status = 'running'",
        (run_id,),
    ).fetchall()
    for step in steps:
        ensure_transition(StepStatus.RUNNING, step_target)
        connection.execute(
            """
            UPDATE model_attempts
            SET status = ?, completed_at = ?,
                error_code = COALESCE(error_code, 'run_terminated')
            WHERE step_id = ? AND status = 'running'
            """,
            (step_target.value, now, step["id"]),
        )
        connection.execute(
            "UPDATE steps SET status = ?, completed_at = ? WHERE id = ? AND status = 'running'",
            (step_target.value, now, step["id"]),
        )
        events.append(append_event(
            connection,
            EventType.STEP_STATUS_CHANGED,
            now,
            {
                "entity_id": step["id"],
                "previous": StepStatus.RUNNING.value,
                "current": step_target.value,
                "reason": "run_terminated",
            },
            session_id=run["session_id"],
            run_id=run_id,
        ))

    if target_status is RunStatus.WAITING_USER_INPUT:
        events.extend(transition_segments(
            connection,
            run_id,
            frozenset({SegmentStatus.RUNNING}),
            SegmentStatus.WAITING_USER_INPUT,
            now,
            "run_paused",
        ))
    else:
        segment_target = (
            SegmentStatus.CANCELED
            if target_status is RunStatus.CANCELED
            else SegmentStatus.FAILED
        )
        events.extend(transition_segments(
            connection,
            run_id,
            frozenset({
                SegmentStatus.QUEUED,
                SegmentStatus.RUNNING,
                SegmentStatus.WAITING_USER_INPUT,
            }),
            segment_target,
            now,
            "run_terminated",
        ))
    connection.execute(
        "UPDATE input_mailbox SET status = 'canceled' WHERE run_id = ? AND status = 'pending'",
        (run_id,),
    )
    return tuple(events)
