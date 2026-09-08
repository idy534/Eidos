from __future__ import annotations

import sqlite3

from eidos_runtime.db.database import now_ms as _now_ms
from eidos_runtime.db.events import append_event
from eidos_runtime.db.transitions import (
    requeue_run_children,
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
    legacy_paused = connection.execute(
        """
        SELECT id, session_id FROM runs
        WHERE status = 'waiting_user_input'
        """
    ).fetchall()
    for row in legacy_paused:
        connection.execute(
            """
            UPDATE execution_segments
            SET status = 'failed', completed_at = ?
            WHERE run_id = ? AND status IN ('queued', 'running', 'waiting_user_input')
            """,
            (now, row["id"]),
        )
        connection.execute(
            """
            UPDATE runs
            SET status = 'interrupted', error_code = 'RUNTIME_INTERRUPTED',
                completed_at = ?, updated_at = ?
            WHERE id = ? AND status = 'waiting_user_input'
            """,
            (now, now, row["id"]),
        )
        append_event(
            connection,
            EventType.RUN_UPDATED,
            now,
            {"reason": "legacy_wait_state_removed"},
            session_id=str(row["session_id"]),
            run_id=str(row["id"]),
        )
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
        """
        CREATE TEMP TABLE requeue_model_runs AS
        SELECT r.id, r.creation_seq
        FROM runs r
        WHERE r.status = 'running'
          AND r.reconciliation_required = 0
          AND r.cancel_requested_at IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM approvals a
              WHERE a.run_id = r.id AND a.status = 'pending'
          )
          AND NOT EXISTS (
              SELECT 1 FROM durable_intents d
              WHERE d.run_id = r.id
                AND d.status IN ('running', 'uncertain', 'interrupted')
          )
          AND NOT EXISTS (
              SELECT 1
              FROM tool_attempts t
              JOIN tool_calls c ON c.id = t.tool_call_id
              JOIN items i ON i.id = c.item_id
              WHERE i.run_id = r.id AND t.status IN ('running', 'uncertain')
          )
          AND NOT EXISTS (
              SELECT 1
              FROM tool_calls c
              JOIN items i ON i.id = c.item_id
              WHERE i.run_id = r.id AND c.status = 'running'
          )
          AND NOT EXISTS (
              SELECT 1 FROM finalization_attempts f
              WHERE f.run_id = r.id AND f.status = 'running'
          )
        """
    )
    # Only a structured approval pause with no unknown execution can resume.
    # A completed network-denied Shell carries its verified result in the
    # approval transaction, so recovery returns that result without replay.
    connection.execute("""
        CREATE TEMP TABLE resumable_approval_runs AS
        SELECT r.id FROM runs r JOIN approvals a ON a.run_id = r.id
        WHERE r.status = 'waiting_approval' AND a.status = 'pending'
          AND r.cancel_requested_at IS NULL AND r.reconciliation_required = 0
          AND json_type(a.request_json, '$.permissionBlockFingerprint') = 'text'
          AND NOT EXISTS (SELECT 1 FROM tool_attempts t
              JOIN tool_calls c ON c.id = t.tool_call_id JOIN items i ON i.id = c.item_id
              WHERE i.run_id = r.id AND t.status = 'running')
          AND NOT EXISTS (SELECT 1 FROM durable_intents d WHERE d.run_id = r.id
              AND d.status IN ('running', 'interrupted', 'uncertain')
              AND NOT COALESCE((d.tool_call_id = a.tool_call_id
                  AND json_extract(a.request_json, '$.kind') = 'permission_request'
                  AND json_extract(a.request_json, '$.completedResult.reconciliationRequired') = 0
                  AND json_extract(a.request_json, '$.completedResult.data.termination') = 'exit'
                  AND json_type(a.request_json, '$.completedResult.data.exitCode') = 'integer'), 0))
    """)
    connection.execute(
        "UPDATE durable_intents SET status = 'interrupted' WHERE status = 'running' "
        "AND run_id NOT IN (SELECT id FROM resumable_approval_runs)"
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
        FROM runs
        WHERE runs.status IN ('running', 'waiting_approval', 'finalizing')
          AND runs.id NOT IN (SELECT id FROM resumable_approval_runs)
          AND (
              EXISTS (
                  SELECT 1 FROM durable_intents
                  WHERE durable_intents.run_id = runs.id
                    AND durable_intents.status IN ('uncertain', 'interrupted')
              )
              OR EXISTS (
                  SELECT 1
                  FROM tool_attempts
                  JOIN tool_calls ON tool_calls.id = tool_attempts.tool_call_id
                  JOIN items ON items.id = tool_calls.item_id
                  WHERE items.run_id = runs.id
                    AND tool_attempts.status = 'uncertain'
              )
          )
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
            RunStatus.INTERRUPTED,
            now,
        )
        run, _event = transition_run(
            connection,
            str(row["id"]),
            frozenset({current}),
            RunStatus.INTERRUPTED,
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

    requeue_runs = connection.execute(
        "SELECT id FROM requeue_model_runs ORDER BY creation_seq"
    ).fetchall()
    for row in requeue_runs:
        run_id = str(row["id"])
        requeue_run_children(
            connection,
            run_id,
            now,
            "runtime_restart_requeue",
        )
        transition_run(
            connection,
            run_id,
            frozenset({RunStatus.RUNNING}),
            RunStatus.QUEUED,
            "runtime_restart_requeue",
        )

    cancel_requested = connection.execute(
        """
        SELECT id, status FROM runs
        WHERE cancel_requested_at IS NOT NULL
          AND cancel_completed_at IS NULL
          AND reconciliation_required = 0
          AND status IN (
              'queued', 'running', 'waiting_approval', 'finalizing'
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
          AND id NOT IN (SELECT id FROM resumable_approval_runs)
          AND id NOT IN (SELECT id FROM requeue_model_runs)
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
          AND approvals.run_id NOT IN (SELECT id FROM resumable_approval_runs)
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
        "UPDATE tool_calls SET approval_status = 'canceled' WHERE approval_status = 'pending' "
        "AND item_id NOT IN (SELECT id FROM items WHERE run_id IN (SELECT id FROM resumable_approval_runs))"
    )
    connection.execute("DROP TABLE requeue_model_runs")
    connection.execute("DROP TABLE resumable_approval_runs")
    verify_runtime_invariants(connection)
