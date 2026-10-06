"""Independent Sessions must not queue behind another Run's long operation."""

from __future__ import annotations

import queue
import threading
from pathlib import Path

import pytest

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.pydantic_ai_client import ModelClientLease
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.runtime.supervisor import RunSupervisor
from eidos_runtime.tools.registry import ToolConcurrencyPolicy


@pytest.mark.parametrize("first_waits_for_approval", [False, True])
def test_nine_same_workspace_runs_execute_without_global_admission_limit(
    tmp_path: Path, first_waits_for_approval: bool,
) -> None:
    store = SessionStore(tmp_path / "data")
    store.initialize()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    entered: queue.Queue[str] = queue.Queue()
    approvals: queue.Queue[dict[str, object]] = queue.Queue()
    release = threading.Event()
    resumed = threading.Event()
    runs = []
    for index in range(9):
        session = store.create_session(str(workspace))
        run, _ = store.enqueue_run(str(session["id"]), f"task-{index}")
        runs.append(str(run["id"]))

    class ControlledEngine(RuntimeEngine):
        def run(self, run_id: str, cancel: threading.Event) -> None:
            entered.put(run_id)
            if run_id == runs[0] and first_waits_for_approval:
                assert self.request_approval is not None
                self.request_approval({"runId": run_id}, cancel)
                resumed.set()
            # Keep every sibling alive while checking admission and resume.
            release.wait(10)

    supervisor = RunSupervisor(
        store, lambda _model_id: ModelClientLease(object()),
        approvals.put, lambda value: value, lambda: True, lambda: False,
        lambda: None, engine_factory=ControlledEngine,
    )
    try:
        supervisor.schedule_next()
        assert {entered.get(timeout=2) for _ in runs} == set(runs)
        if first_waits_for_approval:
            request = approvals.get(timeout=2)
            assert supervisor.submit_approval_response(
                request_id=str(request["id"]), decision="approve", feedback=None,
            )
            # Approval resumes while all eight other workers are still alive.
            assert resumed.wait(2)
        assert not release.is_set()
    finally:
        release.set()
        for run_id in runs:
            supervisor.request_cancel(run_id)
        supervisor.wait(3)
        store.close()


def test_exclusive_effect_in_one_engine_does_not_block_another_engine() -> None:
    # The gate owns one Run, not a workspace, including a currently-held permit.
    first = RuntimeEngine(None, None, lambda _event: None)
    second = RuntimeEngine(None, None, lambda _event: None)
    policy = ToolConcurrencyPolicy(mode="exclusive", max_concurrency=1)
    cancel = threading.Event()
    timeout = threading.Timer(1, cancel.set)
    timeout.start()
    try:
        with first.tool_concurrency_gate.acquire(policy, cancel):
            with second.tool_concurrency_gate.acquire(policy, cancel):
                assert not cancel.is_set()
    finally:
        timeout.cancel()
        timeout.join()
