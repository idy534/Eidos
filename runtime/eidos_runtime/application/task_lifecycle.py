from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


class LifecycleAction(StrEnum):
    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"


@dataclass(frozen=True)
class LifecycleResult:
    action: LifecycleAction
    accepted: bool
    reason: str | None = None


class TaskLifecycleApplication:
    """Thin command boundary; supervisor remains lifecycle authority."""

    def __init__(
        self,
        *,
        pause: Callable[[str], LifecycleResult],
        resume: Callable[[str], LifecycleResult],
        cancel: Callable[[str], LifecycleResult],
    ) -> None:
        self._actions = {
            LifecycleAction.PAUSE: pause,
            LifecycleAction.RESUME: resume,
            LifecycleAction.CANCEL: cancel,
        }

    def execute(self, action: LifecycleAction, run_id: str) -> LifecycleResult:
        if not run_id:
            raise ValueError("run_id is required")
        return self._actions[action](run_id)
