"""Concurrent JSON-RPC request execution with per-resource ordering.

The stdio input loop used to execute every request before reading the next
line, so one slow handler (Git, a long SQLite scan, a lock held by background
work) blocked every later request, including the cheap reads behind a Session
switch. The loop now only parses and classifies; this module runs requests on
the Runtime async kernel with bounded worker capacity.

Admission follows arrival order with read/write keys:

* A write declares ``locks`` (writes sharing a lock run in arrival order;
  ``None`` locks everything, which is the previous total order among writes)
  and ``touches`` (the read scopes that must observe it).
* A read declares ``observes`` and waits only for earlier writes whose
  ``locks`` or ``touches`` intersect it. Writes never wait for reads.

A pipelined client that sends a write and then a read of the same scope still
reads its own write; reads of unrelated scopes never wait behind slow writes.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import itertools
import logging
import time

import anyio
from anyio import get_cancelled_exc_class
from anyio.abc import TaskGroup, TaskStatus

from eidos_runtime.runtime.async_kernel import (
    AsyncKernelError,
    RuntimeAsyncKernel,
    RuntimeAsyncTask,
)


logger = logging.getLogger("eidos.runtime.rpc")

READ_WORKERS = 8
WRITE_WORKERS = 4
SLOW_REQUEST_SECONDS = 1.0
DRAIN_TIMEOUT_SECONDS = 15.0

CONTROL_METHODS = frozenset({"initialize", "runtime/shutdown", "runtime/health"})

# Scopes that reads observe and writes touch.
CONFIG = "config"
SESSIONS = "sessions"
GIT = "git"
TEAMS = "teams"
BROWSER = "browser"

# Reads of one Session: they observe writes to that Session only.
SESSION_READS = frozenset({
    "agent/read",
    "checkpoint/list",
    "event/list",
    "goal/get",
    "hook/history",
    "planning/read",
    "responseAction/state",
    "review/listComments",
    "run/listQueued",
    "session/gitDiff",
    "session/gitRemoteStatus",
    "session/gitStatus",
    "session/gitSuggestCommitMessage",
    "session/read",
    "session/readAround",
})

# Reads of the Session index (sidebar, search).
INDEX_READS = frozenset({"search/sessions", "session/list", "sidebar/listPins"})

# Reads of settings and catalogs; every exclusive write touches them.
CONFIG_READS = frozenset({
    "agent/catalog",
    "agent/definitionRead",
    "channel/read",
    "extension/read",
    "extension/readEvents",
    "guardrails/read",
    "mcp/list",
    "mcp/tools",
    "memory/export",
    "memory/list",
    "memory/read",
    "model/list",
    "model/presets",
    "personalization/read",
    "plugin/list",
    "project/list",
    "project/read",
    "resource/status",
    "schedule/history",
    "settings/read",
    "shellEnvironment/read",
    "skill/detail",
    "skill/list",
    "webSearch/read",
})

# Reads of immutable or externally changing content; they never wait.
FREE_READS = frozenset({
    "browser/status",
    "context/details",
    "context/usage",
    "input/read",
    "input/readAsset",
    "model/discoverCustom",
    "project/gitContext",
    "toolCall/readContent",
    "toolCall/readText",
    "workspace/findFiles",
    "workspace/listDirectory",
    "workspace/readAsset",
    "workspace/readFileForFind",
    "workspace/readFilePreview",
})

TEAM_READS = frozenset({
    "team/memberChanges",
    "team/messages",
})

TEAM_WRITES = frozenset({
    "team/post",
    "team/removeMember",
    "team/setStatus",
    "team/stopMembers",
    "team/taskUpdate",
    "team/updateSettings",
})

BROWSER_WRITES = frozenset({
    "browser/clearExtensionToken",
    "browser/clearProfile",
    "browser/close",
    "browser/open",
    "browser/setExtensionToken",
    "browser/setTarget",
})

PLAN_WRITES = frozenset({
    "plan/edit",
    "plan/read",
})

EXCLUSIVE_WRITES = frozenset({
    "agent/definitionRemove",
    "agent/definitionSave",
    "channel/save",
    "data/backup",
    "guardrails/save",
    "mcp/connection",
    "mcp/create",
    "mcp/remove",
    "mcp/setEnabled",
    "mcp/update",
    "memory/edit",
    "memory/policy",
    "model/create",
    "model/delete",
    "model/qoder",
    "model/update",
    "personalization/save",
    "plugin/activate",
    "plugin/builtin",
    "plugin/import",
    "plugin/remove",
    "plugin/setEnabled",
    "plugin/setHookTrust",
    "project/create",
    "project/createFolders",
    "project/delete",
    "project/update",
    "resource/retry",
    "resource/cleanup",
    "schedule/execute",
    "schedule/refresh",
    "schedule/runNow",
    "settings/update",
    "shellEnvironment/refresh",
    "shellEnvironment/save",
    "skill/remove",
    "skill/setEnabled",
    "webSearch/save",
})

# Git mutations share one lane, as before, and are observed by the Session.
GIT_WRITES = frozenset({
    "session/createBranch",
    "session/gitCommit",
    "session/gitCreateBranch",
    "session/gitFetch",
    "session/gitMerge",
    "session/gitMergeAbort",
    "session/gitPull",
    "session/gitPush",
    "session/gitRebase",
    "session/gitRebaseAbort",
    "session/gitRebaseContinue",
    "session/gitStage",
    "session/gitStageHunk",
    "session/gitDiscard",
    "session/gitUnstage",
    "session/gitSwitchBranch",
})

# Session lifecycle: Git/Worktree lane plus the Session and the index.
LIFECYCLE_WRITES = frozenset({
    "checkpoint/rewind",
    "session/create",
    "session/delete",
    "session/fork",
    "session/handoff",
    "session/restoreWorktree",
})

# Writes scoped to one Session; without a Session id they stay exclusive.
SESSION_WRITES = frozenset({
    "browser/revokeGrant",
    "goal/clear",
    "goal/update",
    "input/prepare",
    "review/createComment",
    "review/deleteComment",
    "run/deleteQueued",
    "run/start",
    "run/steerQueued",
    "run/updateQueued",
    "session/rename",
})

# Run controls must not wait behind unrelated writes (Stop stays responsive).
RUN_WRITES: Mapping[str, str] = {
    "agent/discardWorkspace": "parentRunId",
    "agent/message": "parentRunId",
    "agent/stop": "parentRunId",
    "context/compact": "runId",
    "run/cancel": "runId",
    "run/resume": "runId",
}

EXPLICITLY_CLASSIFIED_METHODS = frozenset(
    CONTROL_METHODS
    | SESSION_READS
    | INDEX_READS
    | CONFIG_READS
    | FREE_READS
    | TEAM_READS
    | GIT_WRITES
    | LIFECYCLE_WRITES
    | SESSION_WRITES
    | TEAM_WRITES
    | BROWSER_WRITES
    | PLAN_WRITES
    | EXCLUSIVE_WRITES
    | set(RUN_WRITES)
    | {
        "input/draftRead",
        "input/draftWrite",
        "team/read",
        "team/confirmRoster",
        "agent/changes",
        "hook/configuration",
        "planning/answer",
        "item/setFeedback",
        "run/revise",
        "sidebar/setPinned",
    }
)


@dataclass(frozen=True)
class RequestClass:
    """Admission rules for one request."""

    lane: str
    locks: frozenset[str] | None = frozenset()
    touches: frozenset[str] = frozenset()
    observes: frozenset[str] = frozenset()

    @property
    def read(self) -> bool:
        return self.lane == "read"

    @property
    def visible(self) -> frozenset[str]:
        return self.touches | (self.locks or frozenset())


EXCLUSIVE = RequestClass("exclusive", None, frozenset({CONFIG, SESSIONS}))
APPROVAL_RESPONSE = RequestClass("keyed", frozenset({"approvals"}))


def classify_request(method: str, params: object) -> RequestClass | None:
    """Return the admission class, or ``None`` for inline control methods."""

    if method in CONTROL_METHODS:
        return None
    values = params if isinstance(params, Mapping) else {}
    session = _scope("session", values.get("sessionId"))
    if method in SESSION_READS:
        return RequestClass("read", observes=session)
    if method in INDEX_READS:
        return RequestClass("read", observes=frozenset({SESSIONS}))
    if method in CONFIG_READS:
        return RequestClass("read", observes=frozenset({CONFIG}))
    if method in FREE_READS:
        return RequestClass("read")
    if method == "team/read":
        return RequestClass("read", observes=session | {TEAMS})
    if method in TEAM_READS:
        team = _scope("team", values.get("teamId"))
        return RequestClass("read", observes=team or frozenset({TEAMS}))
    if method == "agent/changes":
        return RequestClass("read", observes=_scope("run", values.get("parentRunId")))
    if method == "hook/configuration":
        if values.get("action") == "read":
            return RequestClass("read", observes=frozenset({CONFIG}) | session)
        return EXCLUSIVE
    if method == "input/draftRead":
        return RequestClass("read", observes=_scope("draft", values.get("key")))
    if method == "input/draftWrite":
        draft = _scope("draft", values.get("key"))
        return RequestClass("keyed", draft, draft) if draft else EXCLUSIVE
    if method in TEAM_WRITES:
        team = _scope("team", values.get("teamId"))
        return RequestClass("keyed", team, team | {TEAMS}) if team else EXCLUSIVE
    if method == "team/confirmRoster":
        return RequestClass("keyed", frozenset({TEAMS}), frozenset({TEAMS, SESSIONS}))
    if method in PLAN_WRITES:
        plan = _scope("plan", values.get("planId"))
        return RequestClass("keyed", plan, plan | {SESSIONS}) if plan else EXCLUSIVE
    if method == "planning/answer":
        planning = _scope("planning", values.get("requestId"))
        return RequestClass("keyed", planning, planning | {SESSIONS}) if planning else EXCLUSIVE
    if method == "item/setFeedback":
        item = _scope("item", values.get("itemId"))
        return RequestClass("keyed", item, item) if item else EXCLUSIVE
    if method == "run/revise":
        run = _scope("run", values.get("sourceRunId"))
        return RequestClass("keyed", run, frozenset({SESSIONS})) if run else EXCLUSIVE
    if method == "sidebar/setPinned":
        return RequestClass("keyed", frozenset({SESSIONS}), frozenset({SESSIONS}))
    if method in BROWSER_WRITES:
        return RequestClass("keyed", frozenset({BROWSER}), frozenset({BROWSER, CONFIG}))
    if method in EXCLUSIVE_WRITES:
        return EXCLUSIVE
    if method in GIT_WRITES:
        return RequestClass("keyed", frozenset({GIT}), session)
    if method in LIFECYCLE_WRITES:
        return RequestClass("keyed", frozenset({GIT}) | session, session | {SESSIONS})
    if method in SESSION_WRITES:
        if not session:
            return EXCLUSIVE
        return RequestClass("keyed", session, frozenset({SESSIONS}))
    if method in RUN_WRITES:
        run = _scope("run", values.get(RUN_WRITES[method]))
        return RequestClass("keyed", run) if run else EXCLUSIVE
    return EXCLUSIVE


def _scope(prefix: str, value: object) -> frozenset[str]:
    if isinstance(value, str) and value:
        return frozenset({f"{prefix}:{value}"})
    return frozenset()


@dataclass(eq=False)
class RequestEntry:
    sequence: int
    method: str
    request: RequestClass
    run: Callable[[], None]
    reject: Callable[[], None]
    enqueued_at: float = field(default_factory=time.monotonic)
    started_at: float | None = None


class RequestAdmission:
    """Arrival-ordered admission. Confined to one thread; holds no locks."""

    def __init__(self, start: Callable[[RequestEntry], None]) -> None:
        self._start = start
        self._pending: list[RequestEntry] = []
        self._running: list[RequestEntry] = []

    @property
    def idle(self) -> bool:
        return not self._pending and not self._running

    def submit(self, entry: RequestEntry) -> None:
        if entry.request.read and not entry.request.observes:
            self._running.append(entry)
            self._start(entry)
            return
        self._pending.append(entry)
        self._pump()

    def finish(self, entry: RequestEntry) -> None:
        self._running = [running for running in self._running if running is not entry]
        self._pump()

    def take_pending(self) -> tuple[RequestEntry, ...]:
        pending, self._pending = tuple(self._pending), []
        return pending

    def _pump(self) -> None:
        locked: set[str] = set()
        visible: set[str] = set()
        writes_ahead = False
        exclusive_ahead = False

        def record(write: RequestClass) -> None:
            nonlocal writes_ahead, exclusive_ahead
            writes_ahead = True
            if write.locks is None:
                exclusive_ahead = True
            else:
                locked.update(write.locks)
            visible.update(write.visible)

        for running in self._running:
            if not running.request.read:
                record(running.request)
        waiting: list[RequestEntry] = []
        started: list[RequestEntry] = []
        for entry in self._pending:
            request = entry.request
            if request.read:
                if visible.isdisjoint(request.observes):
                    started.append(entry)
                else:
                    waiting.append(entry)
                continue
            if request.locks is None:
                ready = not writes_ahead
            else:
                ready = not exclusive_ahead and locked.isdisjoint(request.locks)
            (started if ready else waiting).append(entry)
            record(request)
        self._pending = waiting
        for entry in started:
            self._running.append(entry)
            self._start(entry)


class RequestDispatcher:
    """Runs admitted requests on the Runtime async kernel."""

    def __init__(
        self,
        kernel: RuntimeAsyncKernel,
        *,
        read_workers: int = READ_WORKERS,
        write_workers: int = WRITE_WORKERS,
        slow_seconds: float = SLOW_REQUEST_SECONDS,
    ) -> None:
        if read_workers < 1 or write_workers < 1:
            raise ValueError("worker capacity must be positive")
        self._kernel = kernel
        self._read_workers = read_workers
        self._write_workers = write_workers
        self._slow_seconds = slow_seconds
        self._sequence = itertools.count(1)
        self._admission: RequestAdmission | None = None
        self._task_group: TaskGroup | None = None
        self._read_limiter: anyio.CapacityLimiter | None = None
        self._write_limiter: anyio.CapacityLimiter | None = None
        self._changed: anyio.Event | None = None
        self._stopped: anyio.Event | None = None
        self._accepting = False
        self._service: RuntimeAsyncTask[None] | None = None

    def start(self) -> None:
        self._service, _started = self._kernel.start_service(
            self._serve, owner_id="rpc-dispatcher"
        )

    def submit(
        self,
        method: str,
        request: RequestClass,
        run: Callable[[], None],
        reject: Callable[[], None],
    ) -> bool:
        """Queue one request; ``False`` means the caller must run it inline."""

        entry = RequestEntry(next(self._sequence), method, request, run, reject)
        try:
            return self._kernel.call(self._submit, entry)
        except (AsyncKernelError, RuntimeError):
            return False
        except Exception:
            # The input loop must keep running; the caller handles it inline.
            logger.exception("Request dispatcher rejected method=%s", method)
            return False

    def drain(self, timeout: float = DRAIN_TIMEOUT_SECONDS) -> bool:
        """Stop accepting, finish queued and running requests, end the service."""

        deadline = time.monotonic() + timeout
        try:
            idle = self._kernel.call(self._drain, timeout)
        except (AsyncKernelError, RuntimeError):
            idle = True
        service = self._service
        if service is None:
            return idle
        return service.wait(max(0.0, deadline - time.monotonic())) and idle

    async def _serve(self, *, task_status: TaskStatus[None]) -> None:
        self._read_limiter = anyio.CapacityLimiter(self._read_workers)
        self._write_limiter = anyio.CapacityLimiter(self._write_workers)
        self._changed = anyio.Event()
        self._stopped = anyio.Event()
        try:
            async with anyio.create_task_group() as task_group:
                self._task_group = task_group
                self._admission = RequestAdmission(self._launch)
                self._accepting = True
                task_status.started()
                try:
                    await self._stopped.wait()
                finally:
                    self._accepting = False
                    for entry in self._admission.take_pending():
                        task_group.start_soon(self._reject, entry)
        finally:
            self._accepting = False
            self._task_group = None

    async def _submit(self, entry: RequestEntry) -> bool:
        if not self._accepting or self._admission is None or self._task_group is None:
            return False
        self._admission.submit(entry)
        return True

    def _launch(self, entry: RequestEntry) -> None:
        assert self._task_group is not None
        self._task_group.start_soon(
            self._execute, entry, name=f"rpc-{entry.sequence}-{entry.method}"
        )

    async def _execute(self, entry: RequestEntry) -> None:
        limiter = self._read_limiter if entry.request.read else self._write_limiter

        def body() -> None:
            entry.started_at = time.monotonic()
            try:
                entry.run()
            except Exception:
                logger.exception("Runtime request failed method=%s", entry.method)

        try:
            await anyio.to_thread.run_sync(body, limiter=limiter)
        except get_cancelled_exc_class():
            if entry.started_at is None:
                with anyio.CancelScope(shield=True):
                    await anyio.to_thread.run_sync(entry.reject)
            raise
        finally:
            self._finished(entry)

    async def _reject(self, entry: RequestEntry) -> None:
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(entry.reject)

    def _finished(self, entry: RequestEntry) -> None:
        if self._admission is not None:
            self._admission.finish(entry)
        if self._changed is not None:
            self._changed.set()
            self._changed = anyio.Event()
        finished = time.monotonic()
        started = entry.started_at
        if started is None:
            return
        waited = started - entry.enqueued_at
        ran = finished - started
        if waited + ran >= self._slow_seconds:
            logger.warning(
                "rpc_slow method=%s lane=%s queue_ms=%.0f exec_ms=%.0f",
                entry.method,
                entry.request.lane,
                waited * 1000,
                ran * 1000,
            )

    async def _drain(self, timeout: float) -> bool:
        self._accepting = False
        with anyio.move_on_after(timeout):
            while self._admission is not None and not self._admission.idle:
                assert self._changed is not None
                await self._changed.wait()
        idle = self._admission is None or self._admission.idle
        if self._stopped is not None:
            self._stopped.set()
        return idle


__all__ = [
    "APPROVAL_RESPONSE",
    "BROWSER_WRITES",
    "CONFIG_READS",
    "CONTROL_METHODS",
    "EXCLUSIVE",
    "EXCLUSIVE_WRITES",
    "EXPLICITLY_CLASSIFIED_METHODS",
    "FREE_READS",
    "GIT_WRITES",
    "INDEX_READS",
    "LIFECYCLE_WRITES",
    "PLAN_WRITES",
    "RUN_WRITES",
    "SESSION_READS",
    "SESSION_WRITES",
    "TEAM_READS",
    "TEAM_WRITES",
    "RequestAdmission",
    "RequestClass",
    "RequestDispatcher",
    "RequestEntry",
    "classify_request",
]
