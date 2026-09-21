from __future__ import annotations

import threading


class DeadlineCancellation(threading.Event):
    def __init__(
        self,
        cancel: threading.Event,
        deadline: float,
        monotonic,
    ) -> None:
        super().__init__()
        self.cancel = cancel
        self.deadline = deadline
        self.monotonic = monotonic
        self.paused_at: float | None = None

    def suspend_deadline(self) -> None:
        if self.paused_at is None:
            self.paused_at = self.monotonic()

    def resume_deadline(self) -> None:
        if self.paused_at is not None:
            self.deadline += max(0.0, self.monotonic() - self.paused_at)
            self.paused_at = None

    def is_set(self) -> bool:
        return self.cancel.is_set() or (
            self.paused_at is None and self.monotonic() >= self.deadline
        )

    def wait(self, timeout: float | None = None) -> bool:
        if self.is_set():
            return True
        remaining = (
            None
            if self.paused_at is not None
            else max(0.0, self.deadline - self.monotonic())
        )
        return (
            self.cancel.wait(
                remaining
                if timeout is None
                else timeout
                if remaining is None
                else min(timeout, remaining)
            )
            or self.is_set()
        )

    @property
    def reason(self) -> str | None:
        if self.cancel.is_set():
            return "cancel"
        if self.paused_at is None and self.monotonic() >= self.deadline:
            return "timeout"
        return None
