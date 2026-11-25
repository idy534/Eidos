from __future__ import annotations

from pathlib import Path
import threading
import time

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import ScriptedModel
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
from eidos_runtime.runtime.supervisor import RunSupervisor


def _make_supervisor(tmp_path: Path) -> tuple[SessionStore, RuntimeAsyncKernel, RunSupervisor]:
    store = SessionStore(tmp_path / "data")
    store.initialize()
    kernel = RuntimeAsyncKernel()
    kernel.start()
    supervisor = RunSupervisor(
        store=store,
        model_for=lambda _model_id: ScriptedModel([]),
        notify=lambda _event: None,
        scan_feedback=lambda value: value,
        can_run=lambda: True,
        shell_available=lambda: False,
        sensitive=lambda: None,
    )
    supervisor.bind_async_kernel(kernel)
    return store, kernel, supervisor


def test_start_managed_task_concurrent_submit_and_fast_completion_does_not_deadlock(
    tmp_path: Path,
) -> None:
    store, kernel, supervisor = _make_supervisor(tmp_path)
    completed: list[str] = []
    lock = threading.Lock()
    try:
        def submit_worker(worker_index: int) -> None:
            for item_index in range(8):
                task_label = f"w{worker_index}-t{item_index}"

                def task(cancel: threading.Event, label: str = task_label) -> None:
                    if cancel.is_set():
                        return
                    if worker_index % 2 == 1:
                        time.sleep(0.005)
                    with lock:
                        completed.append(label)

                started = supervisor.start_managed_task("deadlock-check", task)
                assert started is True

        threads = [
            threading.Thread(target=submit_worker, args=(idx,))
            for idx in range(4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10.0)
            assert not thread.is_alive(), "submitter thread hung waiting on supervisor"

        assert supervisor.wait_managed_tasks(10.0) is True
        assert supervisor.has_active_managed_tasks() is False
        assert len(completed) == 32
        supervisor.resources.ensure_empty()
    finally:
        kernel.close()
        store.close()


def test_start_managed_task_shutdown_cancels_and_drains_cleanly(
    tmp_path: Path,
) -> None:
    store, kernel, supervisor = _make_supervisor(tmp_path)
    started_event = threading.Event()
    observed_cancel = threading.Event()
    try:
        def blocking_task(cancel: threading.Event) -> None:
            started_event.set()
            if cancel.wait(timeout=5.0):
                observed_cancel.set()

        assert supervisor.start_managed_task("blocking-op", blocking_task) is True
        assert started_event.wait(timeout=5.0) is True

        with supervisor.lock:
            for task in supervisor._managed_tasks.values():
                task.cancellation.set()

        assert supervisor.wait_managed_tasks(5.0) is True
        assert observed_cancel.is_set() is True
        assert supervisor.has_active_managed_tasks() is False
        supervisor.resources.ensure_empty()
    finally:
        kernel.close()
        store.close()
