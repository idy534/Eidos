from __future__ import annotations

import sqlite3


class RuntimeInvariantError(RuntimeError):
    pass


def verify_runtime_invariants(connection: sqlite3.Connection) -> None:
    """Fail on persisted lifecycle contradictions; intended for tests and recovery."""
    checks = (
        (
            "terminal_run_has_active_segment",
            """
            SELECT 1 FROM runs JOIN execution_segments ON execution_segments.run_id = runs.id
            WHERE runs.status IN ('succeeded', 'failed', 'stopped', 'canceled', 'interrupted')
              AND execution_segments.status IN ('queued', 'running', 'waiting_user_input')
            LIMIT 1
            """,
        ),
        (
            "terminal_run_has_active_step",
            """
            SELECT 1 FROM runs JOIN steps ON steps.run_id = runs.id
            WHERE runs.status IN ('succeeded', 'failed', 'stopped', 'canceled', 'interrupted')
              AND steps.status = 'running'
            LIMIT 1
            """,
        ),
        (
            "terminal_run_has_active_model_attempt",
            """
            SELECT 1 FROM runs
            JOIN steps ON steps.run_id = runs.id
            JOIN model_attempts ON model_attempts.step_id = steps.id
            WHERE runs.status IN ('succeeded', 'failed', 'stopped', 'canceled', 'interrupted')
              AND model_attempts.status = 'running'
            LIMIT 1
            """,
        ),
        (
            "terminal_run_has_active_finalization_attempt",
            """
            SELECT 1 FROM runs
            JOIN finalization_attempts
              ON finalization_attempts.run_id = runs.id
            WHERE runs.status IN (
                'succeeded', 'failed', 'stopped', 'canceled', 'interrupted'
            )
              AND finalization_attempts.status = 'running'
            LIMIT 1
            """,
        ),
        (
            "terminal_run_has_pending_approval",
            """
            SELECT 1 FROM runs JOIN approvals ON approvals.run_id = runs.id
            WHERE runs.status IN ('succeeded', 'failed', 'stopped', 'canceled', 'interrupted')
              AND approvals.status = 'pending'
            LIMIT 1
            """,
        ),
        (
            "multiple_running_segments",
            """
            SELECT 1 FROM execution_segments WHERE status = 'running'
            GROUP BY run_id HAVING COUNT(*) > 1 LIMIT 1
            """,
        ),
        (
            "multiple_running_steps",
            """
            SELECT 1 FROM steps WHERE status = 'running'
            GROUP BY run_id HAVING COUNT(*) > 1 LIMIT 1
            """,
        ),
        (
            "multiple_running_model_attempts",
            """
            SELECT 1 FROM model_attempts WHERE status = 'running'
            GROUP BY step_id HAVING COUNT(*) > 1 LIMIT 1
            """,
        ),
        (
            "waiting_approval_count",
            """
            SELECT 1 FROM runs
            LEFT JOIN approvals
              ON approvals.run_id = runs.id AND approvals.status = 'pending'
            WHERE runs.status = 'waiting_approval'
            GROUP BY runs.id HAVING COUNT(approvals.id) != 1 LIMIT 1
            """,
        ),
        (
            "pending_approval_item_not_active",
            """
            SELECT 1 FROM approvals JOIN items ON items.id = approvals.item_id
            WHERE approvals.status = 'pending' AND items.status != 'in_progress'
            LIMIT 1
            """,
        ),
        (
            "running_tool_item_not_active",
            """
            SELECT 1 FROM tool_calls JOIN items ON items.id = tool_calls.item_id
            WHERE tool_calls.status = 'running' AND items.status != 'in_progress'
            LIMIT 1
            """,
        ),
        (
            "running_step_without_active_segment",
            """
            SELECT 1 FROM steps
            JOIN execution_segments ON execution_segments.id = steps.segment_id
            WHERE steps.status = 'running'
              AND execution_segments.status NOT IN ('queued', 'running', 'waiting_user_input')
            LIMIT 1
            """,
        ),
        (
            "active_model_attempt_without_active_step",
            """
            SELECT 1 FROM model_attempts JOIN steps ON steps.id = model_attempts.step_id
            WHERE model_attempts.status = 'running' AND steps.status != 'running'
            LIMIT 1
            """,
        ),
    )
    for code, query in checks:
        if connection.execute(query).fetchone() is not None:
            raise RuntimeInvariantError(code)
