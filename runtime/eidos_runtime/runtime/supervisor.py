from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
import logging
import threading
import time
import uuid
from typing import Callable

import anyio
from pydantic import ValidationError

from eidos_runtime.db.storage import (
    InvalidRunStateError,
    ResourceNotFoundError,
    SessionStore,
)
from eidos_runtime.model.client import ModelClient
from eidos_runtime.model.pydantic_ai_client import ModelClientLease
from eidos_runtime.protocol.schemas import ApprovalDecisionDto
from eidos_runtime.runtime.approval import ApprovalDecision
from eidos_runtime.runtime.async_kernel import (
    AsyncKernelClosedError,
    RuntimeAsyncKernel,
    RuntimeAsyncTask,
)
from eidos_runtime.runtime.contracts import RuntimeCancelled
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.runtime.resource_registry import (
    ResourceRegistry,
    RuntimeResource,
    RuntimeResourceDiagnostic,
    RuntimeResourceKind,
)
from eidos_runtime.runtime.fault_injection import hit_fault
from eidos_runtime.runtime.state_machine import RuntimeLifecycle
from eidos_runtime.sandbox.sensitive import SensitiveScanError, SensitiveScanner


logger = logging.getLogger("eidos.runtime")


class RunCancelTimeout(RuntimeError):
    pass


class RunReconciliationRequired(RuntimeError):
    pass


class RuntimeShutdownTimeout(RuntimeError):
    pass


class RunWorkerState(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_SLOT = "waiting_slot"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELING = "canceling"
    FINISHED = "finished"


class RuntimeControlState(StrEnum):
    RUNNING = "running"
    RECONFIGURING = "reconfiguring"
    DRAINING = "draining"


@dataclass
class RunHandle:
    run_id: str
    thread: threading.Thread
    cancellation: threading.Event
    resume: threading.Event
    state: RunWorkerState
    model_lease: ModelClientLease | None = None
    resource: RuntimeResource | None = None


@dataclass
class PendingApproval:
    run_id: str
    event: threading.Event
    decision: ApprovalDecision | None = None


@dataclass
class ManagedTask:
    task_id: str
    kind: str
    cancellation: threading.Event
    async_task_handle: RuntimeAsyncTask[None]
    resource: RuntimeResource
    async_request_resource: RuntimeResource | None


@dataclass(frozen=True)
class WorkerStart:
    run_id: str
    gate: threading.Event


class RunSupervisor:
    """Owns FIFO execution and every Run worker control resource."""

    def __init__(
        self,
        store: SessionStore,
        model_for: Callable[[str], ModelClient | ModelClientLease],
        notify: Callable[[dict[str, object]], None],
        scan_feedback: Callable[[str], str],
        can_run: Callable[[], bool],
        shell_available: Callable[[], bool],
        sensitive: Callable[[], SensitiveScanner | None],
        cleanup: Callable[[], None] | None = None,
        engine_factory: type[RuntimeEngine] = RuntimeEngine,
        *,
        cancel_timeout: float = 6.0,
        shutdown_timeout: float = 6.0,
        resource_registry: ResourceRegistry | None = None,
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
        self.cancel_timeout = cancel_timeout
        self.shutdown_timeout = shutdown_timeout
        self.resources = resource_registry or ResourceRegistry()
        self.lock = threading.RLock()
        self._handles: dict[str, RunHandle] = {}
        self._managed_tasks: dict[str, ManagedTask] = {}
        self._active_slot_run_id: str | None = None
        self.approval_lock = threading.RLock()
        self.pending_approvals: dict[str, PendingApproval] = {}
        self.lifecycle = RuntimeLifecycle.RUNNING
        self.control_state = RuntimeControlState.RUNNING
        self.events = RuntimeEvents(notify, store=store)
        self._async_kernel: RuntimeAsyncKernel | None = None
        self._async_kernel_frozen = False

    def bind_async_kernel(self, kernel: RuntimeAsyncKernel) -> None:
        """Bind the process-owned kernel before the first Run is dispatched."""
        with self.lock:
            if (
                self.lifecycle is not RuntimeLifecycle.RUNNING
                or self.control_state is not RuntimeControlState.RUNNING
                or self._async_kernel_frozen
            ):
                raise RuntimeError("runtime async kernel binding is frozen")
            if self._async_kernel is not None and self._async_kernel is not kernel:
                raise RuntimeError("runtime async kernel is already bound")
            self._async_kernel = kernel

    @property
    def async_kernel(self) -> RuntimeAsyncKernel | None:
        with self.lock:
            return self._async_kernel

    def prepare_next(self) -> WorkerStart | None:
        if (
            self.lifecycle is not RuntimeLifecycle.RUNNING
            or not self.can_run()
            or self.store.health_state != "ready"
        ):
            return None
        with self.lock:
            if (
                self.lifecycle is not RuntimeLifecycle.RUNNING
                or self.control_state is not RuntimeControlState.RUNNING
                or self._active_slot_run_id is not None
            ):
                return None
            claimed = self.store.claim_next_run_committed()
            if claimed is None:
                return None
            run_id = str(claimed.value["id"])
            self._async_kernel_frozen = True
            handle = self._handles.get(run_id)
            if handle is not None:
                if handle.state not in {
                    RunWorkerState.WAITING_APPROVAL,
                    RunWorkerState.WAITING_SLOT,
                }:
                    raise RuntimeError("run already has a worker")
                self._active_slot_run_id = run_id
                handle.state = RunWorkerState.RUNNING
                handle.resume.set()
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
        self.request_cancel(start.run_id)
        start.gate.set()

    def cancel_run(
        self, run_id: str, *, operation_id: str | None = None
    ) -> dict[str, object]:
        hit_fault("cancel_claim_race")
        self.store.read_run(run_id)
        with self.lock:
            handle = self._handles.get(run_id)
            if handle is None:
                return self.store.cancel_run(run_id, operation_id=operation_id)
            mutation = self.store.request_cancel_committed(run_id)
            handle.state = RunWorkerState.CANCEL_REQUESTED
            handle.cancellation.set()
            handle.resume.set()
        self.events.publish(mutation, run=mutation.value)
        self._release_approval_waits(run_id)
        handle.thread.join(timeout=self.cancel_timeout)
        if handle.thread.is_alive():
            failed = self.store.mark_cancel_failed_committed(
                run_id, "RUN_CANCEL_TIMEOUT"
            )
            self.events.publish(failed, run=failed.value)
            raise RunCancelTimeout("RUN_CANCEL_TIMEOUT")
        current = self.store.read_run(run_id)
        if operation_id is not None:
            current = self.store.record_operation_result(
                operation_id,
                "run/cancel",
                {"runId": run_id},
                current,
            )
        if current.get("cancelFailureCode") == "RECONCILIATION_REQUIRED":
            raise RunReconciliationRequired("RECONCILIATION_REQUIRED")
        if current["status"] != "canceled":
            raise InvalidRunStateError("run cancellation did not complete")
        return current

    def request_cancel(self, run_id: str) -> bool:
        with self.lock:
            handle = self._handles.get(run_id)
            if handle is None or handle.state is RunWorkerState.FINISHED:
                return False
            handle.state = RunWorkerState.CANCEL_REQUESTED
            handle.cancellation.set()
            handle.resume.set()
        self._release_approval_waits(run_id)
        return True

    def request_approval(
        self, params: dict[str, object], cancel: threading.Event
    ) -> ApprovalDecision:
        run_id = str(params["runId"])
        request_id = f"server-approval-{uuid.uuid4()}"
        pending = PendingApproval(run_id, threading.Event())
        with self.approval_lock:
            self.pending_approvals[request_id] = pending
        try:
            self._park_active_worker(run_id)
            self.notify({
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "item/requestApproval",
                "params": params,
            })
            while not pending.event.wait(0.1):
                if (
                    cancel.is_set()
                    or self.lifecycle is not RuntimeLifecycle.RUNNING
                ):
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
                    if parsed.feedback is not None
                    else None
                )
                pending.decision = ApprovalDecision(parsed.decision, feedback)
            except (ValidationError, SensitiveScanError):
                pending.decision = ApprovalDecision("reject")
            pending.event.set()

    def wait(self, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        with self.lock:
            workers = tuple(handle.thread for handle in self._handles.values())
        for worker in workers:
            worker.join(timeout=max(0.0, deadline - time.monotonic()))

    def shutdown(self) -> None:
        with self.lock:
            if self.lifecycle in {
                RuntimeLifecycle.QUIESCENT,
                RuntimeLifecycle.CLOSED,
            }:
                return
            self.lifecycle = RuntimeLifecycle.DRAINING
            self.control_state = RuntimeControlState.DRAINING
        run_ids = self.store.nonterminal_run_ids()
        with self.lock:
            handled = frozenset(self._handles)
        for run_id in run_ids:
            if run_id in handled:
                try:
                    mutation = self.store.request_cancel_committed(run_id)
                    self.events.publish(mutation, run=mutation.value)
                except InvalidRunStateError:
                    pass
                self.request_cancel(run_id)
            else:
                try:
                    mutation = self.store.cancel_run_committed(run_id)
                    self.events.publish(mutation, run=mutation.value)
                except InvalidRunStateError:
                    pass
        with self.lock:
            active_ids = tuple(self._handles)
            tasks = tuple(self._managed_tasks.values())
            for task in tasks:
                task.cancellation.set()
        hit_fault("shutdown_tool_completion_race")
        self._release_approval_waits()
        self.wait(self.shutdown_timeout)
        self.wait_managed_tasks(self.shutdown_timeout)
        registered = tuple(
            resource
            for resource in self.resources.active_resources()
            if resource.kind is not RuntimeResourceKind.ASYNC_KERNEL
        )
        if (
            self.has_active_workers()
            or self.has_active_model_leases()
            or self.has_active_managed_tasks()
            or registered
        ):
            for run_id in active_ids:
                try:
                    failed = self.store.mark_cancel_failed_committed(
                        run_id, "RUNTIME_SHUTDOWN_TIMEOUT"
                    )
                    self.events.publish(failed, run=failed.value)
                except (InvalidRunStateError, ResourceNotFoundError):
                    pass
            logger.warning(
                "Runtime shutdown timed out; active resources: %s",
                json.dumps(
                    self._shutdown_diagnostics(registered),
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
            raise RuntimeShutdownTimeout("RUNTIME_SHUTDOWN_TIMEOUT")
        with self.store.lock:
            connection = self.store.connection
            if connection is not None:
                connection.execute("SELECT 1").fetchone()
        with self.lock:
            self.lifecycle = RuntimeLifecycle.QUIESCENT

    def close(self) -> None:
        self.shutdown()
        self.store.close()
        with self.lock:
            self.lifecycle = RuntimeLifecycle.CLOSED

    def has_active_workers(self) -> bool:
        with self.lock:
            return any(
                handle.state is not RunWorkerState.FINISHED
                for handle in self._handles.values()
            )

    def has_active_model_leases(self) -> bool:
        with self.lock:
            return any(
                handle.model_lease is not None and not handle.model_lease.closed
                for handle in self._handles.values()
            )

    def handle_state(self, run_id: str) -> RunWorkerState | None:
        with self.lock:
            handle = self._handles.get(run_id)
            return handle.state if handle is not None else None

    def start_managed_task(
        self,
        kind: str,
        target: Callable[[threading.Event], None],
        *,
        operation_id: str | None = None,
    ) -> bool:
        task_id = str(uuid.uuid4())
        cancellation = threading.Event()
        registered = threading.Event()

        async def run() -> None:
            await anyio.to_thread.run_sync(registered.wait)
            try:
                await anyio.to_thread.run_sync(target, cancellation)
            except Exception:
                resource.fail("MANAGED_TASK_FAILED")
                if async_resource is not None:
                    async_resource.fail("MANAGED_TASK_FAILED")
                logger.exception("Runtime managed task failed: %s", kind)
                raise
            finally:
                if async_resource is not None:
                    async_resource.close()
                resource.close()
                with self.lock:
                    self._managed_tasks.pop(task_id, None)

        resource = self.resources.register(
            RuntimeResourceKind.MANAGED_TASK,
            owner_id=task_id,
            cancel=cancellation.set,
        )
        async_resource = (
            self.resources.register(
                RuntimeResourceKind.ASYNC_REQUEST,
                owner_id=operation_id,
                cancel=cancellation.set,
            )
            if operation_id is not None
            else None
        )
        with self.lock:
            if (
                self.lifecycle is not RuntimeLifecycle.RUNNING
                or self.control_state is not RuntimeControlState.RUNNING
                or self._async_kernel is None
            ):
                if async_resource is not None:
                    async_resource.close()
                resource.close()
                return False
            try:
                handle = self._async_kernel.start_task(
                    run,
                    owner_id=f"managed:{task_id}",
                )
            except (AsyncKernelClosedError, RuntimeError):
                if async_resource is not None:
                    async_resource.close()
                resource.close()
                return False
            self._managed_tasks[task_id] = ManagedTask(
                task_id,
                kind,
                cancellation,
                handle,
                resource,
                async_resource,
            )
            resource.start()
            if async_resource is not None:
                async_resource.start()
            registered.set()
        return True

    def has_active_managed_tasks(self) -> bool:
        with self.lock:
            return any(
                not task.async_task_handle.done()
                for task in self._managed_tasks.values()
            )

    def wait_managed_tasks(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self.lock:
            tasks = tuple(self._managed_tasks.values())
        for task in tasks:
            task.async_task_handle.wait(
                max(0.0, deadline - time.monotonic())
            )
        return not self.has_active_managed_tasks()

    def begin_reconfiguration(self) -> bool:
        hit_fault("configure_worker_race")
        with self.lock:
            if (
                self.lifecycle is not RuntimeLifecycle.RUNNING
                or self.control_state is not RuntimeControlState.RUNNING
                or any(
                    handle.state is not RunWorkerState.FINISHED
                    or (
                        handle.model_lease is not None
                        and not handle.model_lease.closed
                    )
                    for handle in self._handles.values()
                )
                or any(
                    not task.async_task_handle.done()
                    for task in self._managed_tasks.values()
                )
            ):
                return False
            self.control_state = RuntimeControlState.RECONFIGURING
            return True

    def end_reconfiguration(self) -> None:
        with self.lock:
            if self.control_state is RuntimeControlState.RECONFIGURING:
                self.control_state = RuntimeControlState.RUNNING

    def _shutdown_diagnostics(
        self,
        registered: tuple[RuntimeResourceDiagnostic, ...],
    ) -> list[dict[str, object]]:
        diagnostics = [
            {
                "kind": resource.kind.value,
                "owner_id": resource.owner_id,
                "state": resource.state.value,
                "deadline": resource.deadline,
                "diagnostic_code": resource.diagnostic_code,
            }
            for resource in registered[:100]
        ]
        represented = {
            (str(value["kind"]), str(value["owner_id"]))
            for value in diagnostics
        }
        with self.lock:
            handles = tuple(self._handles.values())
            managed = tuple(self._managed_tasks.values())
        for handle in handles:
            if len(diagnostics) >= 100:
                break
            if (
                handle.state is not RunWorkerState.FINISHED
                and (RuntimeResourceKind.RUN_WORKER.value, handle.run_id)
                not in represented
            ):
                diagnostics.append({
                    "kind": RuntimeResourceKind.RUN_WORKER.value,
                    "owner_id": handle.run_id,
                    "state": handle.state.value,
                    "deadline": None,
                    "diagnostic_code": "RUNTIME_SHUTDOWN_TIMEOUT",
                })
            if (
                len(diagnostics) < 100
                and handle.model_lease is not None
                and not handle.model_lease.closed
                and (RuntimeResourceKind.MODEL_LEASE.value, handle.run_id)
                not in represented
            ):
                diagnostics.append({
                    "kind": RuntimeResourceKind.MODEL_LEASE.value,
                    "owner_id": handle.run_id,
                    "state": "running",
                    "deadline": None,
                    "diagnostic_code": "RUNTIME_SHUTDOWN_TIMEOUT",
                })
        for task in managed:
            if len(diagnostics) >= 100:
                break
            if (
                not task.async_task_handle.done()
                and (RuntimeResourceKind.MANAGED_TASK.value, task.task_id)
                not in represented
            ):
                diagnostics.append({
                    "kind": RuntimeResourceKind.MANAGED_TASK.value,
                    "owner_id": task.task_id,
                    "state": task.async_task_handle.state.value,
                    "deadline": task.async_task_handle.deadline,
                    "diagnostic_code": (
                        task.async_task_handle.diagnostics().diagnostic_code
                    ),
                })
        return diagnostics

    def _release_approval_waits(self, run_id: str | None = None) -> None:
        with self.approval_lock:
            for pending in tuple(self.pending_approvals.values()):
                if run_id is not None and pending.run_id != run_id:
                    continue
                if not pending.event.is_set():
                    pending.decision = ApprovalDecision("reject")
                    pending.event.set()

    def _start_worker_locked(self, run_id: str) -> WorkerStart:
        if run_id in self._handles:
            raise RuntimeError("run already has a worker")
        cancellation = threading.Event()
        resume = threading.Event()
        gate = threading.Event()
        worker = threading.Thread(
            target=self._run_worker,
            args=(run_id, cancellation, gate),
            name=f"eidos-run-{run_id}",
        )
        resource = self.resources.register(
            RuntimeResourceKind.RUN_WORKER,
            owner_id=run_id,
            cancel=cancellation.set,
        )
        self._handles[run_id] = RunHandle(
            run_id=run_id,
            thread=worker,
            cancellation=cancellation,
            resume=resume,
            state=RunWorkerState.STARTING,
            resource=resource,
        )
        self._active_slot_run_id = run_id
        worker.start()
        resource.start()
        return WorkerStart(run_id, gate)

    def _run_worker(
        self,
        run_id: str,
        cancellation: threading.Event,
        start_gate: threading.Event,
    ) -> None:
        with self.lock:
            handle = self._handles.get(run_id)
            if handle is None:
                handle = RunHandle(
                    run_id,
                    threading.current_thread(),
                    cancellation,
                    threading.Event(),
                    RunWorkerState.STARTING,
                )
                self._handles[run_id] = handle
                self._active_slot_run_id = run_id
        start_gate.wait()
        try:
            self.store.read_run(run_id)
            leased = self.model_for(run_id)
            lease = (
                leased
                if isinstance(leased, ModelClientLease)
                else ModelClientLease(leased)
            )
            with self.lock:
                handle.model_lease = lease
                handle.state = RunWorkerState.RUNNING
            engine_kwargs: dict[str, object] = {
                "sensitive": self.sensitive(),
                "wait_for_execution_slot": self._wait_for_execution_slot,
                "resource_registry": self.resources,
                "events": self.events,
            }
            if self.engine_factory is RuntimeEngine:
                engine_kwargs["async_kernel"] = self._async_kernel
            engine = self.engine_factory(
                self.store,
                lease.client,
                self.notify,
                self.request_approval,
                self.shell_available(),
                **engine_kwargs,
            )
            if isinstance(engine, RuntimeEngine):
                engine.terminalize_cancel = False
            engine.run(run_id, cancellation)
        except RuntimeCancelled:
            with self.lock:
                handle.state = RunWorkerState.CANCELING
        except Exception:
            if cancellation.is_set():
                with self.lock:
                    handle.state = RunWorkerState.CANCELING
            else:
                self._fail_worker(run_id)
        finally:
            try:
                if self.cleanup is not None:
                    self.cleanup()
            finally:
                lease = handle.model_lease
                if lease is not None:
                    lease.close()
            if cancellation.is_set():
                try:
                    mutation = self.store.complete_requested_cancel_committed(run_id)
                    items = {
                        str(item["id"]): item
                        for item in self.store.canceled_items_for_run(run_id)
                    }
                    self.events.publish(
                        mutation, run=mutation.value, items=items
                    )
                except InvalidRunStateError:
                    pass
                except Exception:
                    logger.exception("Run cancellation finalization failed")
            should_schedule = False
            with self.lock:
                handle.state = RunWorkerState.FINISHED
                if self._active_slot_run_id == run_id:
                    self._active_slot_run_id = None
                self._handles.pop(run_id, None)
                should_schedule = self.lifecycle is RuntimeLifecycle.RUNNING
            if handle.resource is not None:
                handle.resource.close()
            if should_schedule:
                self.schedule_next()

    def _fail_worker(self, run_id: str) -> None:
        try:
            logger.exception("Run worker failed")
            run = self.store.read_run(run_id)
            if run["status"] in {
                "running",
                "waiting_approval",
                "finalizing",
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

    def _park_active_worker(self, run_id: str) -> None:
        with self.lock:
            handle = self._handles.get(run_id)
            if (
                handle is None
                or handle.thread is not threading.current_thread()
                or self._active_slot_run_id != run_id
            ):
                return
            handle.state = RunWorkerState.WAITING_APPROVAL
            self._active_slot_run_id = None
        self.schedule_next()

    def _wait_for_execution_slot(
        self, run_id: str, cancellation: threading.Event
    ) -> bool:
        with self.lock:
            handle = self._handles.get(run_id)
            if handle is None:
                return False
            if self._active_slot_run_id == run_id:
                handle.state = RunWorkerState.RUNNING
                return True
            handle.state = RunWorkerState.WAITING_SLOT
            handle.resume.clear()
        self.schedule_next()
        while not handle.resume.wait(0.1):
            if (
                cancellation.is_set()
                or self.lifecycle is not RuntimeLifecycle.RUNNING
            ):
                return False
        return not cancellation.is_set()
