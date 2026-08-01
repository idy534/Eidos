from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.task_lifecycle import (
    LifecycleAction,
    LifecycleResult,
    RuntimeLifecyclePort,
    TaskLifecycleApplication,
)
from eidos_runtime.domain.long_task import LongTaskProgress


@dataclass
class RecordingRuntimeLifecycle:
    calls: list[tuple[LifecycleAction, str]] = field(default_factory=list)
    status_calls: list[str] = field(default_factory=list)

    def pause_run(self, run_id: str) -> LifecycleResult:
        self.calls.append((LifecycleAction.PAUSE, run_id))
        return LifecycleResult(action=LifecycleAction.PAUSE, accepted=True)

    def resume_run(self, run_id: str) -> LifecycleResult:
        self.calls.append((LifecycleAction.RESUME, run_id))
        return LifecycleResult(action=LifecycleAction.RESUME, accepted=True)

    def cancel_run(
        self, run_id: str, *, operation_id: str | None = None
    ) -> LifecycleResult:
        del operation_id
        self.calls.append((LifecycleAction.CANCEL, run_id))
        return LifecycleResult(action=LifecycleAction.CANCEL, accepted=True)

    def run_status(self, run_id: str) -> LongTaskProgress | None:
        self.status_calls.append(run_id)
        return None


@pytest.mark.parametrize(
    "action",
    (LifecycleAction.PAUSE, LifecycleAction.RESUME, LifecycleAction.CANCEL),
)
def test_task_lifecycle_application_dispatches_each_action_to_explicit_runtime_port(
    action: LifecycleAction,
) -> None:
    runtime = RecordingRuntimeLifecycle()
    application = TaskLifecycleApplication(runtime)

    result = application.execute(action, "run-1")

    assert isinstance(runtime, RuntimeLifecyclePort)
    assert result == LifecycleResult(action=action, accepted=True)
    assert runtime.calls == [(action, "run-1")]


def test_task_lifecycle_application_rejects_blank_run_ids_before_runtime_call() -> None:
    runtime = RecordingRuntimeLifecycle()
    application = TaskLifecycleApplication(runtime)

    with pytest.raises(ApplicationError, match="run_id is required") as error:
        application.execute(LifecycleAction.CANCEL, "")

    assert error.value.code == "INVALID_STATE"
    assert runtime.calls == []


def test_task_lifecycle_status_uses_the_runtime_owned_read_boundary() -> None:
    runtime = RecordingRuntimeLifecycle()

    result = TaskLifecycleApplication(runtime).status("run-1")

    assert result is None
    assert runtime.status_calls == ["run-1"]
