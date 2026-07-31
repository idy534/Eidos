from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from enum import StrEnum
from functools import partial
from threading import RLock
from typing import Any, ParamSpec, TypeVar

from anyio.from_thread import BlockingPortal, start_blocking_portal

from eidos_runtime.runtime.resource_registry import (
    ResourceRegistry,
    RuntimeResource,
    RuntimeResourceKind,
)


P = ParamSpec("P")
T = TypeVar("T")


class AsyncKernelState(StrEnum):
    NEW = "new"
    RUNNING = "running"
    CLOSING = "closing"
    CLOSED = "closed"
    FAILED = "failed"


class AsyncKernelError(RuntimeError):
    pass


class AsyncKernelClosedError(AsyncKernelError):
    pass


class AsyncKernelCloseError(AsyncKernelError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class RuntimeAsyncKernel:
    """Process-owned portal for synchronous Runtime callers of async I/O."""

    def __init__(self, *, resource_registry: ResourceRegistry | None = None) -> None:
        self._resources = resource_registry
        self._resource: RuntimeResource | None = None
        self._portal_context: AbstractContextManager[BlockingPortal] | None = None
        self._portal: BlockingPortal | None = None
        self._state = AsyncKernelState.NEW
        self._lock = RLock()

    @property
    def state(self) -> AsyncKernelState:
        with self._lock:
            return self._state

    @property
    def portal_identity(self) -> int | None:
        with self._lock:
            return id(self._portal) if self._portal is not None else None

    def start(self) -> None:
        with self._lock:
            if self._state is AsyncKernelState.RUNNING:
                return
            if self._state is not AsyncKernelState.NEW:
                raise AsyncKernelClosedError("runtime async kernel is not available")
            if self._resources is not None:
                self._resource = self._resources.register(
                    RuntimeResourceKind.ASYNC_KERNEL,
                    owner_id="runtime",
                )
            try:
                context = start_blocking_portal(
                    backend="asyncio",
                    name="eidos-runtime-async",
                )
                self._portal = context.__enter__()
                self._portal_context = context
            except BaseException:
                self._state = AsyncKernelState.FAILED
                if self._resource is not None:
                    self._resource.fail("ASYNC_KERNEL_START_FAILED")
                raise
            self._state = AsyncKernelState.RUNNING
            if self._resource is not None:
                self._resource.start()

    def call(
        self,
        function: Callable[P, Awaitable[T]],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> T:
        with self._lock:
            if self._state is not AsyncKernelState.RUNNING or self._portal is None:
                raise AsyncKernelClosedError("runtime async kernel is not available")
            portal = self._portal
        try:
            return portal.call(partial(function, *args, **kwargs))
        except RuntimeError:
            with self._lock:
                if self._state is not AsyncKernelState.RUNNING:
                    raise AsyncKernelClosedError(
                        "runtime async kernel is not available"
                    ) from None
            raise

    def close(self) -> None:
        with self._lock:
            if self._state is AsyncKernelState.CLOSED:
                return
            if self._state is AsyncKernelState.CLOSING:
                return
            portal = self._portal
            context = self._portal_context
            self._state = AsyncKernelState.CLOSING
        try:
            if portal is not None:
                portal.call(partial(portal.stop, cancel_remaining=True))
            if context is not None:
                context.__exit__(None, None, None)
        except BaseException as error:
            with self._lock:
                self._state = AsyncKernelState.FAILED
                if self._resource is not None:
                    self._resource.fail("ASYNC_KERNEL_SHUTDOWN_FAILED")
            raise AsyncKernelCloseError("ASYNC_KERNEL_SHUTDOWN_FAILED") from error
        with self._lock:
            self._portal = None
            self._portal_context = None
            self._state = AsyncKernelState.CLOSED
            resource = self._resource
            self._resource = None
        if resource is not None:
            resource.close()
