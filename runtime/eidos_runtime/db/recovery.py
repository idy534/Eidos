from __future__ import annotations

import sqlite3

from eidos_runtime.db.database import now_ms as _now_ms
from eidos_runtime.db.events import append_event
from eidos_runtime.db.transitions import (
    settle_run_children,
    transition_run,
    transition_segments,
)
from eidos_runtime.runtime.state_machine import (
    ApprovalStatus,
    EventType,
    RunStatus,
    SegmentStatus,
    ensure_transition,
)


def recover_runtime_facts(connection: sqlite3.Connection) -> None:
    """Recover abandoned persisted facts without consulting in-memory phases."""
    now = _now_ms()
    connection.execute(
        "UPDATE durable_intents SET status = 'interrupted' WHERE status = 'running'"
    )
    reconciliation_runs = connection.execute(
        """
        SELECT DISTINCT runs.id, runs.status
        FROM runs JOIN durable_intents ON durable_intents.run_id = runs.id
        WHERE durable_intents.status = 'interrupted'
          AND runs.status IN ('running', 'waiting_approval', 'finalizing')
        """
    ).fetchall()
    for row in reconciliation_runs:
        current = RunStatus(row["status"])
        connection.execute(
            """
            UPDATE runs
            SET reconciliation_required = 1,
                reconciliation_epoch = reconciliation_epoch + 1,
                side_effects_may_exist = 1
            WHERE id = ? AND status = ?
            """,
            (row["id"], current.value),
        )
        transition_segments(
            connection,
            str(row["id"]),
            frozenset({SegmentStatus.RUNNING}),
            SegmentStatus.WAITING_USER_INPUT,
            now,
            "side_effect_reconciliation_required",
        )
        run, _event = transition_run(
            connection,
            str(row["id"]),
            frozenset({current}),
            RunStatus.WAITING_USER_INPUT,
            "side_effect_reconciliation_required",
        )
        epoch = connection.execute(
            "SELECT reconciliation_epoch FROM runs WHERE id = ?", (row["id"],)
        ).fetchone()["reconciliation_epoch"]
        append_event(
            connection,
            EventType.RECONCILIATION_REQUIRED,
            now,
            {
                "epoch": int(epoch),
                "reason": "runtime_restart",
            },
            session_id=str(run["sessionId"]),
            run_id=str(run["id"]),
        )

    active_runs = connection.execute(
        """
        SELECT id, status FROM runs
        WHERE status IN ('running', 'waiting_approval', 'finalizing')
        """
    ).fetchall()
    for row in active_runs:
        settle_run_children(connection, str(row["id"]), RunStatus.INTERRUPTED, now)
        transition_run(
            connection,
            str(row["id"]),
            frozenset({RunStatus(row["status"])}),
            RunStatus.INTERRUPTED,
            "runtime_interrupted",
        )

    approvals = connection.execute(
        """
        SELECT approvals.id, approvals.run_id, runs.session_id
        FROM approvals JOIN runs ON runs.id = approvals.run_id
        WHERE approvals.status = 'pending'
        """
    ).fetchall()
    for approval in approvals:
        ensure_transition(ApprovalStatus.PENDING, ApprovalStatus.INVALIDATED)
        connection.execute(
            "UPDATE approvals SET status = 'invalidated', decided_at = ? WHERE id = ? AND status = 'pending'",
            (now, approval["id"]),
        )
        append_event(
            connection,
            EventType.APPROVAL_STATUS_CHANGED,
            now,
            {
                "entity_id": approval["id"],
                "previous": ApprovalStatus.PENDING.value,
                "current": ApprovalStatus.INVALIDATED.value,
                "reason": "runtime_restart",
            },
            session_id=approval["session_id"],
            run_id=approval["run_id"],
        )
    connection.execute(
        "UPDATE tool_calls SET approval_status = 'canceled' WHERE approval_status = 'pending'"
    )
