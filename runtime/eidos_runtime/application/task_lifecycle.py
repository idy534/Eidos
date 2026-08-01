from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.results import ApplicationResult


class LifecycleAction(StrEnum):
    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"


@dataclass(frozen=True)
class LifecycleResult(ApplicationResult):
    action: LifecycleAction
    accepted: bool
    reason: str | None = None


@runtime_checkable
class RuntimeLifecyclePort(Protocol):
    """Runtime-owned lifecycle commands used by the application boundary."""

    def pause_run(self, run_id: str) -> LifecycleResult: ...

    def resume_run(self, run_id: str) -> LifecycleResult: ...

    def cancel_run(
        self, run_id: str, *, operation_id: str | None = None
    ) -> LifecycleResult: ...


class TaskLifecycleApplication:
    """Thin command boundary; supervisor remains lifecycle authority."""

    def __init__(self, runtime: RuntimeLifecyclePort) -> None:
        self._runtime = runtime

    def execute(
        self,
        action: LifecycleAction,
        run_id: str,
        *,
        operation_id: str | None = None,
    ) -> LifecycleResult:
        if not run_id:
            raise ApplicationError("INVALID_STATE", "run_id is required")
        if action is LifecycleAction.PAUSE:
            return self._runtime.pause_run(run_id)
        if action is LifecycleAction.RESUME:
            return self._runtime.resume_run(run_id)
        if action is LifecycleAction.CANCEL:
            return self._runtime.cancel_run(run_id, operation_id=operation_id)
        raise ApplicationError("INVALID_STATE", "unsupported lifecycle action")


__all__ = [
    "LifecycleAction",
    "LifecycleResult",
    "RuntimeLifecyclePort",
    "TaskLifecycleApplication",
]
