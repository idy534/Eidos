from __future__ import annotations

from dataclasses import dataclass
import logging
import threading
import uuid
from typing import Callable

from pydantic import ValidationError

from eidos_runtime.db.storage import (
    InvalidRunStateError,
    ResourceNotFoundError,
    SessionStore,
)
from eidos_runtime.model.client import ModelClient
from eidos_runtime.protocol.schemas import ApprovalDecisionDto
from eidos_runtime.runtime.approval import ApprovalDecision
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.sandbox.sensitive import SensitiveScanError, SensitiveScanner


logger = logging.getLogger("eidos.runtime")


@dataclass
class PendingApproval:
    event: threading.Event
    decision: ApprovalDecision | None = None


@dataclass
class WaitingWorker:
    thread: threading.Thread
    cancellation: threading.Event
    resume: threading.Event


@dataclass(frozen=True)
class WorkerStart:
    run_id: str
    gate: threading.Event


class RunSupervisor:
    """Owns FIFO execution, worker lifetime, approval waits, and cleanup."""

    def __init__(
        self,
        store: SessionStore,
        model_for: Callable[[str], ModelClient],
        notify: Callable[[dict[str, object]], None],
        scan_feedback: Callable[[str], str],
        can_run: Callable[[], bool],
        shell_available: Callable[[], bool],
        sensitive: Callable[[], SensitiveScanner | None],
        cleanup: Callable[[], None] | None = None,
        engine_factory: type[RuntimeEngine] = RuntimeEngine,
    ) -> None:
        self.store = store
        self.model_for = model_for
        self.notify = notify
        self.scan_feedback = scan_feedback
        self.can_run = can_run
        self.shell_available = shell_available
        self.sensitive = sensitive
        self.cleanup = cleanup
        self.engine_factory = engine_factory
        self.lock = threading.RLock()
        self.worker: threading.Thread | None = None
        self.active_run_id: str | None = None
        self.active_cancel: threading.Event | None = None
        self.waiting_workers: dict[str, WaitingWorker] = {}
        self.approval_lock = threading.RLock()
        self.pending_approvals: dict[str, PendingApproval] = {}
        self.shutting_down = False
        self.events = RuntimeEvents(notify)

    def prepare_next(self) -> WorkerStart | None:
        if not self.can_run() or self.store.health_state != "ready":
            return None
        with self.lock:
            if self.worker is not None and self.worker.is_alive():
                return None
            claimed = self.store.claim_next_run_committed()
            if claimed is None:
                return None
            run_id = str(claimed.value["id"])
            waiting = self.waiting_workers.pop(run_id, None)
            if waiting is not None:
                self.active_run_id = run_id
                self.active_cancel = waiting.cancellation
                self.worker = waiting.thread
                waiting.resume.set()
                return None
            return self._start_worker_locked(run_id)

    def schedule_next(self) -> None:
        start = self.prepare_next()
        if start is not None:
            start.gate.set()

    @staticmethod
    def release(start: WorkerStart | None) -> None:
        if start is not None:
            start.gate.set()

    def abort(self, start: WorkerStart | None) -> None:
        if start is None:
            return
        with self.lock:
            if self.active_run_id == start.run_id and self.active_cancel is not None:
                self.active_cancel.set()
        start.gate.set()

    def cancel_run(
        self, run_id: str, *, operation_id: str | None = None
    ) -> dict[str, object]:
        current = self.store.read_run(run_id)
        if current["status"] == "waiting_approval":
            try:
                mutation = self.store.cancel_waiting_approval_committed(run_id)
            except InvalidRunStateError:
                return self.store.read_run(run_id)
            with self.lock:
                if self.active_run_id == run_id and self.active_cancel is not None:
                    self.active_cancel.set()
                waiting = self.waiting_workers.get(run_id)
                if waiting is not None:
                    waiting.cancellation.set()
            items = {
                str(item["id"]): item
                for item in self.store.canceled_items_for_run(run_id)
            }
            self.events.publish(mutation, run=mutation.value, items=items)
            return mutation.value
        with self.lock:
            if self.active_run_id == run_id and self.active_cancel is not None:
                self.active_cancel.set()
                worker = self.worker
            elif run_id in self.waiting_workers:
                waiting = self.waiting_workers[run_id]
                waiting.cancellation.set()
                worker = waiting.thread
            else:
                worker = None
        if worker is not None:
            worker.join(timeout=6.0)
        current = self.store.read_run(run_id)
        if current["status"] in {
            "queued", "running", "waiting_approval", "waiting_user_input"
        }:
            return self.store.cancel_run(run_id, operation_id=operation_id)
        return current

    def request_approval(
        self, params: dict[str, object], cancel: threading.Event
    ) -> ApprovalDecision:
        request_id = f"server-approval-{uuid.uuid4()}"
        pending = PendingApproval(threading.Event())
        with self.approval_lock:
            self.pending_approvals[request_id] = pending
        try:
            self._park_active_worker(str(params["runId"]), cancel)
            self.notify({
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "item/requestApproval",
                "params": params,
            })
            while not pending.event.wait(0.1):
                if cancel.is_set() or self.shutting_down:
                    return ApprovalDecision("reject")
            return pending.decision or ApprovalDecision("reject")
        finally:
            with self.approval_lock:
                self.pending_approvals.pop(request_id, None)

    def handle_approval_response(self, message: dict[str, object]) -> None:
        request_id = message.get("id")
        if not isinstance(request_id, str):
            return
        with self.approval_lock:
            pending = self.pending_approvals.get(request_id)
            if pending is None or pending.event.is_set():
                return
            try:
                parsed = ApprovalDecisionDto.model_validate(message.get("result"))
                feedback = (
                    self.scan_feedback(parsed.feedback)
                    if parsed.feedback is not None else None
                )
                pending.decision = ApprovalDecision(parsed.decision, feedback)
            except (ValidationError, SensitiveScanError):
                pending.decision = ApprovalDecision("reject")
            pending.event.set()

    def wait(self, timeout: float = 5.0) -> None:
        with self.lock:
            workers = [self.worker] + [
                entry.thread for entry in self.waiting_workers.values()
            ]
        for worker in {worker for worker in workers if worker is not None}:
            worker.join(timeout=timeout)

    def shutdown(self) -> None:
        self.shutting_down = True
        self._cancel_waits()
        self.wait(6.0)
        if not self._workers_alive():
            self.store.close()

    def close(self) -> None:
        with self.lock:
            active_run_id = self.active_run_id
        self._cancel_waits()
        if active_run_id is not None and not self.shutting_down:
            try:
                self.store.interrupt_run(active_run_id)
            except (ResourceNotFoundError, InvalidRunStateError):
                pass
        self.wait(6.0)
        if not self._workers_alive():
            self.store.close()

    def has_active_workers(self) -> bool:
        with self.lock:
            return self.worker is not None and self.worker.is_alive()

    def _cancel_waits(self) -> None:
        with self.lock:
            if self.active_cancel is not None:
                self.active_cancel.set()
            waiting = tuple(self.waiting_workers.values())
            for entry in waiting:
                entry.cancellation.set()
        with self.approval_lock:
            approvals = tuple(self.pending_approvals.values())
            for pending in approvals:
                if not pending.event.is_set():
                    pending.decision = ApprovalDecision("reject")
                    pending.event.set()

    def _workers_alive(self) -> bool:
        with self.lock:
            workers = [self.worker] + [
                entry.thread for entry in self.waiting_workers.values()
            ]
        return any(worker is not None and worker.is_alive() for worker in workers)

    def _start_worker_locked(self, run_id: str) -> WorkerStart:
        cancellation = threading.Event()
        gate = threading.Event()
        worker = threading.Thread(
            target=self._run_worker,
            args=(run_id, cancellation, gate),
            name=f"eidos-run-{run_id}",
            daemon=True,
        )
        self.active_run_id = run_id
        self.active_cancel = cancellation
        self.worker = worker
        worker.start()
        return WorkerStart(run_id, gate)

    def _run_worker(
        self,
        run_id: str,
        cancellation: threading.Event,
        start_gate: threading.Event,
    ) -> None:
        start_gate.wait()
        try:
            run = self.store.read_run(run_id)
            self.engine_factory(
                self.store,
                self.model_for(str(run["modelId"])),
                self.notify,
                self.request_approval,
                self.shell_available(),
                sensitive=self.sensitive(),
                wait_for_execution_slot=self._wait_for_execution_slot,
            ).run(run_id, cancellation)
        except Exception:
            logger.exception("Run worker failed")
            try:
                run = self.store.read_run(run_id)
                if run["status"] in {
                    "running", "waiting_approval", "finalizing"
                }:
                    mutation = self.store.fail_run_committed(
                        run_id, "INTERNAL_ERROR"
                    )
                    items = {
                        str(item["id"]): item
                        for item in self.store.canceled_items_for_run(run_id)
                    }
                    self.events.publish(
                        mutation, run=mutation.value, items=items
                    )
            except Exception:
                logger.exception("Run worker cleanup failed")
        finally:
            if self.cleanup is not None:
                self.cleanup()
            should_schedule = False
            with self.lock:
                if self.active_run_id == run_id:
                    self.active_run_id = None
                    self.active_cancel = None
                    self.worker = None
                    should_schedule = not self.shutting_down
                waiting = self.waiting_workers.pop(run_id, None)
                if waiting is not None:
                    should_schedule = should_schedule or not self.shutting_down
            if should_schedule:
                self.schedule_next()

    def _park_active_worker(
        self, run_id: str, cancellation: threading.Event
    ) -> None:
        with self.lock:
            if (
                self.active_run_id != run_id
                or self.worker is not threading.current_thread()
            ):
                return
            self.waiting_workers[run_id] = WaitingWorker(
                thread=self.worker,
                cancellation=cancellation,
                resume=threading.Event(),
            )
            self.active_run_id = None
            self.active_cancel = None
            self.worker = None
        self.schedule_next()

    def _wait_for_execution_slot(
        self, run_id: str, cancellation: threading.Event
    ) -> bool:
        with self.lock:
            waiting = self.waiting_workers.get(run_id)
        if waiting is None:
            return False
        self.schedule_next()
        while not waiting.resume.wait(0.1):
            if cancellation.is_set() or self.shutting_down:
                return False
        return not cancellation.is_set()
