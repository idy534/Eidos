from __future__ import annotations

import sqlite3

from eidos_runtime.db.database import now_ms as _now_ms
from eidos_runtime.db.events import append_event
from eidos_runtime.db.transitions import (
    settle_run_children,
    transition_run,
)
from eidos_runtime.db.invariants import verify_runtime_invariants
from eidos_runtime.runtime.state_machine import (
    ApprovalStatus,
    EventType,
    RunStatus,
    ensure_transition,
)


def recover_runtime_facts(connection: sqlite3.Connection) -> None:
    """Recover abandoned persisted facts without consulting in-memory phases."""
    now = _now_ms()
    connection.execute(
        """
        UPDATE async_operations
        SET status = 'interrupted',
            error_code = 'ASYNC_OPERATION_INTERRUPTED',
            completed_at = ?
        WHERE status IN ('accepted', 'running')
        """,
        (now,),
    )
    connection.execute(
        "UPDATE durable_intents SET status = 'interrupted' WHERE status = 'running'"
    )
    connection.execute(
        """
        UPDATE tool_attempts
        SET status = 'uncertain', completed_at = ?,
            result_code = 'runtime_interrupted'
        WHERE status = 'running'
        """,
        (now,),
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
        settle_run_children(
            connection,
            str(row["id"]),
            RunStatus.WAITING_USER_INPUT,
            now,
        )
        run, _event = transition_run(
            connection,
            str(row["id"]),
            frozenset({current}),
            RunStatus.WAITING_USER_INPUT,
            "side_effect_reconciliation_required",
        )
        connection.execute(
            """
            UPDATE runs SET cancel_failure_code = 'RECONCILIATION_REQUIRED'
            WHERE id = ? AND cancel_requested_at IS NOT NULL
            """,
            (row["id"],),
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

    cancel_requested = connection.execute(
        """
        SELECT id, status FROM runs
        WHERE cancel_requested_at IS NOT NULL
          AND cancel_completed_at IS NULL
          AND reconciliation_required = 0
          AND status IN (
              'queued', 'running', 'waiting_approval',
              'waiting_user_input', 'finalizing'
          )
        """
    ).fetchall()
    for row in cancel_requested:
        settle_run_children(connection, str(row["id"]), RunStatus.CANCELED, now)
        transition_run(
            connection,
            str(row["id"]),
            frozenset({RunStatus(row["status"])}),
            RunStatus.CANCELED,
            "cancel_recovered",
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

    paused_runs = connection.execute(
        "SELECT id FROM runs WHERE status = 'waiting_user_input'"
    ).fetchall()
    for row in paused_runs:
        settle_run_children(
            connection,
            str(row["id"]),
            RunStatus.WAITING_USER_INPUT,
            now,
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
            """
            UPDATE approvals SET status = 'invalidated', decided_at = ?
            WHERE id = ? AND status = 'pending'
            """,
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
    verify_runtime_invariants(connection)
