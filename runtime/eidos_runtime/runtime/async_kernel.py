from __future__ import annotations

from collections import deque
from collections.abc import Awaitable, Callable
from concurrent.futures import CancelledError, Future, InvalidStateError
from contextlib import AbstractContextManager
from enum import StrEnum
from functools import partial
from threading import Event, Lock, RLock
import time
from typing import Generic, ParamSpec, TypeVar, cast
import uuid

from anyio import get_cancelled_exc_class
from anyio.abc import TaskStatus
from anyio.from_thread import BlockingPortal, start_blocking_portal
from pydantic import BaseModel, ConfigDict

from eidos_runtime.runtime.resource_registry import (
    ResourceRegistry,
    RuntimeResource,
    RuntimeResourceKind,
)


P = ParamSpec("P")
T = TypeVar("T")
S = TypeVar("S")


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


class AsyncTaskState(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class RuntimeAsyncTaskDiagnostic(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    task_id: str
    owner_id: str
    state: AsyncTaskState
    deadline: float | None
    diagnostic_code: str | None


_UNSET = object()


class RuntimeAsyncTask(Generic[T]):
    """Synchronous handle for one task owned by the Runtime async kernel."""

    def __init__(
        self,
        *,
        task_id: str,
        owner_id: str,
        deadline: float | None,
    ) -> None:
        self.task_id = task_id
        self.owner_id = owner_id
        self.deadline = deadline
        self._state = AsyncTaskState.CREATED
        self._diagnostic_code: str | None = None
        self._result: object = _UNSET
        self._exception: BaseException | None = None
        self._future: Future[None] | None = None
        self._resource: RuntimeResource | None = None
        self._completion = Event()
        self._settlement_started = False
        self._lock = RLock()

    @property
    def state(self) -> AsyncTaskState:
        with self._lock:
            return self._state

    def cancel(self) -> bool:
        with self._lock:
            if self._state in {
                AsyncTaskState.COMPLETED,
                AsyncTaskState.FAILED,
                AsyncTaskState.CANCELED,
                AsyncTaskState.CANCEL_REQUESTED,
            }:
                return False
            self._state = AsyncTaskState.CANCEL_REQUESTED
            resource = self._resource
            future = self._future
        if resource is not None:
            resource.cancel()
        if future is not None:
            future.cancel()
        return True

    def wait(self, timeout: float | None = None) -> bool:
        return self._completion.wait(timeout)

    def done(self) -> bool:
        return self._completion.is_set()

    def result(self) -> T:
        if not self.done():
            raise InvalidStateError("async task has not completed")
        with self._lock:
            state = self._state
            result = self._result
            error = self._exception
        if state is AsyncTaskState.CANCELED:
            raise CancelledError()
        if state is AsyncTaskState.FAILED:
            assert error is not None
            raise error
        if state is not AsyncTaskState.COMPLETED or result is _UNSET:
            raise InvalidStateError("async task has no terminal result")
        return cast(T, result)

    def exception(self) -> BaseException | None:
        if not self.done():
            raise InvalidStateError("async task has not completed")
        with self._lock:
            state = self._state
            error = self._exception
        if state is AsyncTaskState.CANCELED:
            raise CancelledError()
        return error

    def diagnostics(self) -> RuntimeAsyncTaskDiagnostic:
        with self._lock:
            return RuntimeAsyncTaskDiagnostic(
                task_id=self.task_id,
                owner_id=self.owner_id,
                state=self._state,
                deadline=self.deadline,
                diagnostic_code=self._diagnostic_code,
            )

    def _bind_resource(self, resource: RuntimeResource) -> None:
        with self._lock:
            self._resource = resource

    def _bind_future(self, future: Future[None]) -> None:
        with self._lock:
            self._future = future
            cancel_requested = self._state is AsyncTaskState.CANCEL_REQUESTED
        if cancel_requested:
            future.cancel()

    def _mark_running(self) -> None:
        with self._lock:
            if self._state is AsyncTaskState.CREATED:
                self._state = AsyncTaskState.RUNNING
                resource = self._resource
                if resource is not None:
                    resource.start()

    def _complete(self, result: T) -> None:
        with self._lock:
            self._state = AsyncTaskState.COMPLETED
            self._result = result
            self._exception = None

    def _fail(self, error: BaseException, diagnostic_code: str) -> None:
        with self._lock:
            self._state = AsyncTaskState.FAILED
            self._result = _UNSET
            self._exception = error
            self._diagnostic_code = diagnostic_code

    def _mark_canceled(self) -> None:
        with self._lock:
            self._state = AsyncTaskState.CANCELED
            self._result = _UNSET
            self._exception = None

    def _mark_shutdown_timeout(self, diagnostic_code: str) -> None:
        with self._lock:
            self._diagnostic_code = diagnostic_code
            resource = self._resource
        if resource is not None:
            resource.fail(diagnostic_code)

    def _begin_settlement(self) -> bool:
        with self._lock:
            if self._settlement_started:
                return False
            self._settlement_started = True
            return True

    def _settled(self) -> None:
        self._completion.set()


class _TaskStatusProxy(Generic[S]):
    def __init__(self, task_status: TaskStatus[S]) -> None:
        self._task_status = task_status
        self.started_called = False

    def started(self, value: S | None = None) -> None:
        self._task_status.started(value)
        self.started_called = True


class RuntimeAsyncKernel:
    """Process-owned portal and lifecycle owner for Runtime async work."""

    def __init__(
        self,
        *,
        resource_registry: ResourceRegistry | None = None,
        task_shutdown_timeout: float = 5.0,
        max_task_diagnostics: int = 100,
    ) -> None:
        if task_shutdown_timeout < 0:
            raise ValueError("task_shutdown_timeout must be non-negative")
        if max_task_diagnostics <= 0:
            raise ValueError("max_task_diagnostics must be positive")
        self._resources = resource_registry
        self._resource: RuntimeResource | None = None
        self._portal_context: AbstractContextManager[BlockingPortal] | None = None
        self._portal: BlockingPortal | None = None
        self._task_shutdown_timeout = task_shutdown_timeout
        self._tasks: dict[str, RuntimeAsyncTask[object]] = {}
        self._recent_task_diagnostics: deque[RuntimeAsyncTaskDiagnostic] = deque(
            maxlen=max_task_diagnostics
        )
        self._state = AsyncKernelState.NEW
        self._lock = RLock()
        self._close_lock = Lock()

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

    def start_task(
        self,
        function: Callable[P, Awaitable[T]],
        *args: P.args,
        owner_id: str,
        deadline: float | None = None,
        **kwargs: P.kwargs,
    ) -> RuntimeAsyncTask[T]:
        task, resource, portal = self._prepare_task(owner_id, deadline)
        operation = partial(function, *args, **kwargs)
        try:
            future = portal.start_task_soon(
                self._run_owned_task,
                task,
                resource,
                operation,
                name=f"eidos-async-task-{task.task_id}",
            )
            task._bind_future(future)
        except BaseException as error:
            self._fail_task_start(task, resource, error)
            raise
        return task

    def start_service(
        self,
        function: Callable[..., Awaitable[T]],
        *args: object,
        owner_id: str,
        deadline: float | None = None,
        **kwargs: object,
    ) -> tuple[RuntimeAsyncTask[T], object]:
        task, resource, portal = self._prepare_task(owner_id, deadline)

        async def operation(task_status: TaskStatus[object]) -> T:
            return await function(*args, task_status=task_status, **kwargs)

        try:
            future, started = portal.start_task(
                self._run_owned_service,
                task,
                resource,
                operation,
                name=f"eidos-async-service-{task.task_id}",
            )
            task._bind_future(future)
        except BaseException as error:
            if not task.done():
                self._fail_task_start(task, resource, error)
            raise
        return task, started

    def recent_task_diagnostics(self) -> tuple[RuntimeAsyncTaskDiagnostic, ...]:
        with self._lock:
            return tuple(self._recent_task_diagnostics)

    def close(self) -> None:
        with self._close_lock:
            with self._lock:
                if self._state is AsyncKernelState.CLOSED:
                    return
                portal = self._portal
                context = self._portal_context
                tasks = tuple(self._tasks.values())
                self._state = AsyncKernelState.CLOSING
            for task in tasks:
                task.cancel()
            deadline = time.monotonic() + self._task_shutdown_timeout
            for task in tasks:
                task.wait(max(0.0, deadline - time.monotonic()))
            timed_out = tuple(task for task in tasks if not task.done())
            if timed_out:
                code = "ASYNC_KERNEL_TASK_SHUTDOWN_TIMEOUT"
                for task in timed_out:
                    task._mark_shutdown_timeout(code)
                with self._lock:
                    self._state = AsyncKernelState.FAILED
                    if self._resource is not None:
                        self._resource.fail(code)
                raise AsyncKernelCloseError(code)
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
                raise AsyncKernelCloseError(
                    "ASYNC_KERNEL_SHUTDOWN_FAILED"
                ) from error
            with self._lock:
                self._portal = None
                self._portal_context = None
                self._state = AsyncKernelState.CLOSED
                resource = self._resource
                self._resource = None
            if resource is not None:
                resource.close()

    def _prepare_task(
        self,
        owner_id: str,
        deadline: float | None,
    ) -> tuple[RuntimeAsyncTask[T], RuntimeResource | None, BlockingPortal]:
        with self._lock:
            if self._state is not AsyncKernelState.RUNNING or self._portal is None:
                raise AsyncKernelClosedError("runtime async kernel is not available")
            task = RuntimeAsyncTask[T](
                task_id=str(uuid.uuid4()),
                owner_id=owner_id,
                deadline=deadline,
            )
            resource = (
                self._resources.register(
                    RuntimeResourceKind.ASYNC_TASK,
                    owner_id=owner_id,
                    task_id=task.task_id,
                    deadline=deadline,
                )
                if self._resources is not None
                else None
            )
            if resource is not None:
                task._bind_resource(resource)
            self._tasks[task.task_id] = cast(RuntimeAsyncTask[object], task)
            return task, resource, self._portal

    async def _run_owned_task(
        self,
        task: RuntimeAsyncTask[T],
        resource: RuntimeResource | None,
        operation: Callable[[], Awaitable[T]],
    ) -> None:
        task._mark_running()
        try:
            result = await operation()
        except BaseException as error:
            if isinstance(error, get_cancelled_exc_class()):
                task._mark_canceled()
            else:
                task._fail(error, "ASYNC_TASK_FAILED")
                if resource is not None:
                    resource.fail("ASYNC_TASK_FAILED")
        else:
            task._complete(result)
        finally:
            self._finalize_task(task, resource)

    async def _run_owned_service(
        self,
        task: RuntimeAsyncTask[T],
        resource: RuntimeResource | None,
        operation: Callable[[TaskStatus[object]], Awaitable[T]],
        *,
        task_status: TaskStatus[object],
    ) -> None:
        task._mark_running()
        status = _TaskStatusProxy(task_status)
        startup_error: BaseException | None = None
        try:
            result = await operation(status)
            if not status.started_called:
                raise RuntimeError("Task exited without calling task_status.started()")
        except BaseException as error:
            if isinstance(error, get_cancelled_exc_class()):
                task._mark_canceled()
            else:
                code = (
                    "ASYNC_TASK_FAILED"
                    if status.started_called
                    else "ASYNC_TASK_START_FAILED"
                )
                task._fail(error, code)
                if resource is not None:
                    resource.fail(code)
            if not status.started_called:
                startup_error = error
        else:
            task._complete(result)
        finally:
            self._finalize_task(task, resource)
        if startup_error is not None:
            raise startup_error

    def _fail_task_start(
        self,
        task: RuntimeAsyncTask[object],
        resource: RuntimeResource | None,
        error: BaseException,
    ) -> None:
        task._fail(error, "ASYNC_TASK_START_FAILED")
        if resource is not None:
            resource.fail("ASYNC_TASK_START_FAILED")
        self._finalize_task(task, resource)

    def _finalize_task(
        self,
        task: RuntimeAsyncTask[object],
        resource: RuntimeResource | None,
    ) -> None:
        if not task._begin_settlement():
            return
        try:
            if resource is not None:
                resource.close()
        except Exception as error:
            task._fail(error, "ASYNC_TASK_RESOURCE_CLOSE_FAILED")
            if resource is not None:
                resource.fail("ASYNC_TASK_RESOURCE_CLOSE_FAILED")
        finally:
            diagnostic = task.diagnostics()
            with self._lock:
                self._tasks.pop(task.task_id, None)
                self._recent_task_diagnostics.append(diagnostic)
            task._settled()
