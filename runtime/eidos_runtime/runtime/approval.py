from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Callable


@dataclass(frozen=True)
class ApprovalRequest:
    payload: dict[str, object]


@dataclass(frozen=True)
class ApprovalResult:
    decision: str
    feedback: str | None = None


class ApprovalAdapter:
    """Keeps approval transport details outside the RuntimeEngine implementation."""

    def __init__(
        self,
        request: Callable[[dict[str, object], threading.Event], object] | None,
    ) -> None:
        self._request = request

    def request(self, request: ApprovalRequest, cancel: threading.Event) -> ApprovalResult:
        if self._request is None:
            return ApprovalResult("reject")
        value = self._request(request.payload, cancel)
        return ApprovalResult(
            str(getattr(value, "decision", "reject")),
            getattr(value, "feedback", None),
        )
