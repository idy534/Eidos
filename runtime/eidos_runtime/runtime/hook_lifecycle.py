"""Session ownership for Hook resources; SQLite remains the execution journal."""
from __future__ import annotations

from functools import partial
import threading
import time
import uuid
from typing import TYPE_CHECKING

import anyio

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.hooks import HookBinding, HookResult
from eidos_runtime.persistence.hooks import HookRepository
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel, RuntimeAsyncTask
from eidos_runtime.runtime.contracts import RuntimeCancelled
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.runtime.hook_transcript import discard_transcript
from eidos_runtime.runtime.resource_registry import ResourceRegistry, RuntimeResourceKind
from eidos_runtime.sandbox.sensitive import SensitiveScanner

if TYPE_CHECKING:
    from eidos_runtime.runtime.hooks import HookService


class HookLifecycle:
    def __init__(self, store: SessionStore, resources: ResourceRegistry, events: RuntimeEvents,
                 sensitive: SensitiveScanner, kernel: RuntimeAsyncKernel | None = None) -> None:
        self.store, self.resources, self.events, self.sensitive = store, resources, events, sensitive
        self.kernel = kernel
        self.key = uuid.uuid4().hex
        self._sessions: dict[str, str] = {}
        self._background: dict[str, tuple[str, threading.Event, threading.Event, RuntimeAsyncTask[None]]] = {}
        self._limiters: dict[str, anyio.CapacityLimiter] = {}
        self._lock = threading.RLock()

    def enter_run(self, run_id: str) -> str | None:
        run = self.store.read_run(run_id)
        session_id = str(run["sessionId"])
        with self._lock:
            opened = session_id in self._sessions
            self._sessions[session_id] = run_id
        if opened:
            return None
        return HookRepository(self.store.database).session_start_source(run_id)

    def submit(self, service: HookService, binding: HookBinding, event_key: str, input_json: str) -> None:
        repository = HookRepository(self.store.database)
        invocation_id, _saved, created = repository.reserve(service.run_id, binding, event_key, input_json)
        if not created:
            return
        session_id = str(self.store.read_run(service.run_id)["sessionId"])
        with self._lock:
            for identifier, value in tuple(self._background.items()):
                if value[3].done():
                    self._background.pop(identifier)
            if self.kernel is None or sum(value[0] == session_id for value in self._background.values()) >= 256:
                repository.complete(invocation_id, HookResult(status="failed", error_code="hook_background_capacity"))
                return
            registered, done, cancel = threading.Event(), threading.Event(), threading.Event()
            resource = self.resources.register(RuntimeResourceKind.MANAGED_TASK, "hook:" + session_id,
                cancel=cancel.set, wait=done.wait, is_quiescent=done.is_set)

            def execute() -> None:
                registered.wait()
                try:
                    service._execute(binding, event_key, input_json, cancel, invocation_id)
                    self.events.deliver_pending()
                finally:
                    done.set()
                    resource.close()

            async def run() -> None:
                limiter = self._limiters.setdefault(session_id, anyio.CapacityLimiter(8))
                # Accepted work must record cancellation even if it was still queued.
                with anyio.CancelScope(shield=True):
                    await anyio.to_thread.run_sync(execute, limiter=limiter)

            try:
                handle = self.kernel.start_task(run, owner_id="hook:" + session_id)
                self._background[invocation_id] = (session_id, cancel, done, handle)
                resource.start()
            except Exception:
                done.set()
                resource.close()
                repository.complete(invocation_id, HookResult(status="failed", error_code="hook_background_unavailable"))
            finally:
                registered.set()

    def end_session(self, session_id: str) -> None:
        repository = HookRepository(self.store.database)
        # A parent Session owns borrowed child Sessions and their background work.
        for child in repository.child_sessions(session_id):
            self.end_session(child)
        with self._lock:
            run_id = self._sessions.pop(session_id, None)
            pending = [value for value in self._background.values() if value[0] == session_id]
        for _session, cancel, _done, _handle in pending:
            cancel.set()
        deadline = time.monotonic() + 5
        for _session, _cancel, done, handle in pending:
            if not done.wait(max(0, deadline - time.monotonic())) or not handle.wait(max(0, deadline - time.monotonic())):
                with self._lock:
                    if run_id is not None:
                        self._sessions.setdefault(session_id, run_id)
                raise RuntimeError("hook_shutdown_timeout")
        with self._lock:
            for identifier, value in tuple(self._background.items()):
                if value[0] == session_id and value[3].done():
                    self._background.pop(identifier)
            self._limiters.pop(session_id, None)
        repository.discard_background(session_id)
        if run_id is None:
            discard_transcript(self.store, session_id)
            return
        from eidos_runtime.persistence.collaboration import CollaborationRepository
        if CollaborationRepository(self.store.database).child_for_run(run_id):
            discard_transcript(self.store, session_id)
            return
        from eidos_runtime.runtime.hooks import HookService
        from eidos_runtime.runtime.tool_execution import _DeadlineCancellation
        try:
            service = HookService(self.store, run_id, resources=self.resources, sensitive=self.sensitive,
                events=self.events, async_kernel=self.kernel)
            service.dispatch("SessionEnd", "session-end:" + self.key,
                _DeadlineCancellation(threading.Event(), time.monotonic() + 3, time.monotonic),
                names=("other",), fields={"reason": "other"})
        except RuntimeCancelled:
            pass
        finally:
            discard_transcript(self.store, session_id)

    def close(self) -> None:
        with self._lock:
            sessions = tuple(self._sessions)
            for _session, cancel, _done, _handle in self._background.values():
                cancel.set()
        if self.kernel is None:
            for session_id in sessions:
                self.end_session(session_id)
            return

        async def close_sessions() -> None:
            async with anyio.create_task_group() as group:
                for session_id in sessions:
                    group.start_soon(partial(anyio.to_thread.run_sync, self.end_session, session_id))

        self.kernel.call(close_sessions)
