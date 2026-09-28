"""Non-authoritative observations of durable Run loop boundaries.

These events are for diagnostics. They are not permission hooks and must never
decide whether a tool or model request is allowed to run.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field


logger = logging.getLogger("eidos.runtime")


class LoopStage(StrEnum):
    BEFORE_MODEL = "before_model"
    AFTER_MODEL = "after_model"
    BEFORE_TOOL_BATCH = "before_tool_batch"
    AFTER_TOOL_BATCH = "after_tool_batch"
    BEFORE_COMPACT = "before_compact"
    RUN_EXITED = "run_exited"


class LoopLifecycleEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    stage: LoopStage
    run_id: str
    step_id: str | None = None
    model_attempt_id: str | None = None
    tool_count: int = Field(default=0, ge=0)
    reason: str | None = None


class LoopLifecycle:
    """Emits bounded metadata after or before the named orchestration boundary."""

    def __init__(
        self, observer: Callable[[LoopLifecycleEvent], None] | None = None,
    ) -> None:
        self._observer = observer

    def emit(
        self,
        stage: LoopStage,
        run_id: str,
        *,
        step_id: str | None = None,
        model_attempt_id: str | None = None,
        tool_count: int = 0,
        reason: str | None = None,
    ) -> None:
        event = LoopLifecycleEvent(
            stage=stage,
            run_id=run_id,
            step_id=step_id,
            model_attempt_id=model_attempt_id,
            tool_count=tool_count,
            reason=reason,
        )
        logger.debug(
            "loop_lifecycle_boundary",
            extra={
                "run_id": run_id,
                "step_id": step_id,
                "model_attempt_id": model_attempt_id,
                "stage": stage.value,
                "tool_count": tool_count,
                "reason": reason,
            },
        )
        if self._observer is None:
            return
        try:
            self._observer(event)
        except Exception as error:
            # A diagnostic observer is never a second authority for the Run.
            logger.warning(
                "loop_lifecycle_observer_failed",
                extra={
                    "run_id": run_id,
                    "step_id": step_id,
                    "stage": stage.value,
                    "error_type": type(error).__name__,
                },
            )
