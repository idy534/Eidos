from __future__ import annotations

from enum import StrEnum
import threading
import time
import uuid
from typing import Callable

from pydantic import BaseModel, ConfigDict


class ResourceRegistryError(RuntimeError):
    pass


class RuntimeResourceKind(StrEnum):
    RUN_WORKER = "run_worker"
    MANAGED_TASK = "managed_task"
    ASYNC_KERNEL = "async_kernel"
    MODEL_LEASE = "model_lease"
    TOOL_EXECUTION = "tool_execution"
    SHELL_PROCESS = "shell_process"
    MCP_CONNECTION = "mcp_connection"
    MCP_COMMAND = "mcp_command"
    FINALIZATION = "finalization"
    ASYNC_REQUEST = "async_request"


class RuntimeResourceState(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    CLOSING = "closing"
    CLOSED = "closed"
    FAILED = "failed"


class RuntimeResourceDiagnostic(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    resource_id: str
    kind: RuntimeResourceKind
    owner_id: str
    state: RuntimeResourceState
    created_at: int
    started_at: int | None = None
    closed_at: int | None = None
    deadline: float | None = None
    diagnostic_code: str | None = None


class RuntimeResource:
    def __init__(
        self,
        registry: ResourceRegistry,
        diagnostic: RuntimeResourceDiagnostic,
        *,
        cancel: Callable[[], None] | None,
        close: Callable[[], None] | None,
        wait: Callable[[float | None], bool] | None,
        is_quiescent: Callable[[], bool] | None,
    ) -> None:
        self._registry = registry
        self._diagnostic = diagnostic
        self._cancel = cancel
        self._close = close
        self._wait = wait
        self._is_quiescent = is_quiescent
        self._lock = threading.RLock()

    @property
    def resource_id(self) -> str:
        return self._diagnostic.resource_id

    def start(self) -> None:
        self._update(
            state=RuntimeResourceState.RUNNING,
            started_at=_now_ms(),
        )

    def cancel(self) -> None:
        with self._lock:
            if self._diagnostic.state is RuntimeResourceState.CLOSED:
                return
            self._update(state=RuntimeResourceState.CANCEL_REQUESTED)
            if self._cancel is not None:
                self._cancel()

    def close(self) -> None:
        with self._lock:
            if self._diagnostic.state is RuntimeResourceState.CLOSED:
                return
            self._update(state=RuntimeResourceState.CLOSING)
            try:
                if self._close is not None:
                    self._close()
            except Exception as error:
                self.fail(type(error).__name__)
                raise
            if not self.is_quiescent():
                self.fail("RUNTIME_RESOURCE_NOT_QUIESCENT")
                raise ResourceRegistryError("RUNTIME_RESOURCE_NOT_QUIESCENT")
            self._update(
                state=RuntimeResourceState.CLOSED,
                closed_at=_now_ms(),
                diagnostic_code=None,
            )
            self._registry._release(self.resource_id)

    def wait(self, timeout: float | None = None) -> bool:
        if self._wait is not None:
            return self._wait(timeout)
        return self.is_quiescent()

    def is_quiescent(self) -> bool:
        if self._is_quiescent is not None:
            return self._is_quiescent()
        return True

    def diagnostics(self) -> RuntimeResourceDiagnostic:
        with self._lock:
            return self._diagnostic

    def fail(self, diagnostic_code: str) -> None:
        self._update(
            state=RuntimeResourceState.FAILED,
            diagnostic_code=diagnostic_code,
        )

    def _update(self, **changes: object) -> None:
        with self._registry._lock:
            self._diagnostic = self._diagnostic.model_copy(update=changes)


class ResourceRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._resources: dict[str, RuntimeResource] = {}

    def register(
        self,
        kind: RuntimeResourceKind,
        owner_id: str,
        *,
        resource_id: str | None = None,
        deadline: float | None = None,
        cancel: Callable[[], None] | None = None,
        close: Callable[[], None] | None = None,
        wait: Callable[[float | None], bool] | None = None,
        is_quiescent: Callable[[], bool] | None = None,
    ) -> RuntimeResource:
        identifier = resource_id or str(uuid.uuid4())
        with self._lock:
            if identifier in self._resources:
                raise ResourceRegistryError("resource already registered")
            resource = RuntimeResource(
                self,
                RuntimeResourceDiagnostic(
                    resource_id=identifier,
                    kind=kind,
                    owner_id=owner_id,
                    state=RuntimeResourceState.CREATED,
                    created_at=_now_ms(),
                    deadline=deadline,
                ),
                cancel=cancel,
                close=close,
                wait=wait,
                is_quiescent=is_quiescent,
            )
            self._resources[identifier] = resource
            return resource

    def active_resources(self) -> tuple[RuntimeResourceDiagnostic, ...]:
        with self._lock:
            return tuple(
                resource.diagnostics()
                for resource in self._resources.values()
                if resource.diagnostics().state is not RuntimeResourceState.CLOSED
            )

    def ensure_empty(self) -> None:
        if self.active_resources():
            raise ResourceRegistryError("RUNTIME_RESOURCE_NOT_QUIESCENT")

    def _release(self, resource_id: str) -> None:
        with self._lock:
            self._resources.pop(resource_id, None)


def _now_ms() -> int:
    return time.time_ns() // 1_000_000
