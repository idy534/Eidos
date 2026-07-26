from __future__ import annotations

import sqlite3
import threading

from eidos_runtime.db.invariants import verify_runtime_invariants
from eidos_runtime.runtime.tool_execution import active_tool_execution_count


FAULT_POINTS = frozenset({
    "model_stream_block",
    "model_cancel_delay",
    "tool_block",
    "tool_late_result",
    "shell_ignore_sigterm",
    "shell_modify_then_fail",
    "mcp_ignore_protocol_cancel",
    "mcp_thread_stuck",
    "workspace_manifest_timeout",
    "sqlite_append_event_failure",
    "sqlite_commit_failure",
    "finalization_model_failure",
    "jsonrpc_output_disconnect",
    "cancel_claim_race",
    "configure_worker_race",
    "cancel_approval_race",
    "cancel_finalization_race",
    "shutdown_tool_completion_race",
})


class FaultInjectionHarness:
    """Deterministic barriers and failures shared by runtime fault tests."""

    def __init__(self) -> None:
        self._entered = {
            point: threading.Event() for point in FAULT_POINTS
        }
        self._release = {
            point: threading.Event() for point in FAULT_POINTS
        }
        self._errors: dict[str, BaseException] = {}

    def fail_with(self, point: str, error: BaseException) -> None:
        self._check(point)
        self._errors[point] = error

    def trigger(self, point: str, timeout: float = 2.0) -> None:
        self._check(point)
        self._entered[point].set()
        error = self._errors.get(point)
        if error is not None:
            raise error
        if not self._release[point].wait(timeout):
            raise TimeoutError(f"fault point did not release: {point}")

    def wait_until_entered(self, point: str, timeout: float = 2.0) -> bool:
        self._check(point)
        return self._entered[point].wait(timeout)

    def release(self, point: str) -> None:
        self._check(point)
        self._release[point].set()

    @staticmethod
    def _check(point: str) -> None:
        if point not in FAULT_POINTS:
            raise ValueError(f"unknown fault point: {point}")


def assert_runtime_converged(store, supervisor=None) -> None:
    with store.lock:
        connection: sqlite3.Connection | None = store.connection
        if connection is None:
            raise AssertionError("store is closed")
        verify_runtime_invariants(connection)
        event_ids = [
            int(row[0])
            for row in connection.execute(
                "SELECT id FROM events ORDER BY id"
            ).fetchall()
        ]
    if event_ids != sorted(set(event_ids)):
        raise AssertionError("event ids are not strictly monotonic")
    if active_tool_execution_count():
        raise AssertionError("live tool execution")
    if supervisor is not None and (
        supervisor.has_active_workers()
        or supervisor.has_active_model_leases()
        or supervisor.has_active_managed_tasks()
    ):
        raise AssertionError("live supervisor resource")
    if any(
        thread.is_alive()
        and thread.name.startswith(("eidos-mcp-", "eidos-title-"))
        for thread in threading.enumerate()
    ):
        raise AssertionError("live runtime thread")
