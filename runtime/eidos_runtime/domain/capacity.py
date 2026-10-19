"""Eidos capacity policy: request limits suspend work instead of failing a Run."""
from __future__ import annotations


class CapacityExceeded(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def model_output_byte_budget(max_output_tokens: int) -> int:
    # A defensive memory allowance, not a tokenizer or a Provider capability.
    # Hitting it suspends the Run; it never admits an unbounded response.
    return max(512 * 1024, max_output_tokens * 128)


def is_capacity_error(code: str, outcome: str) -> bool:
    if outcome == "success":
        return False
    code = code.lower()
    return (
        code.endswith(("_too_large", "_limit", "_limit_exceeded", "_budget_exceeded", "_capacity_exceeded"))
        or code in {"tool_output_too_large", "agent_task_limit", "invalid_line_range", "tool_not_activated"}
    )


def is_argument_capacity(reason: str | None) -> bool:
    return bool(reason and (
        reason.endswith(("_too_large", "_limit"))
        or reason in {"too_long", "string_too_long", "less_than_equal", "invalid_line_range"}
    ))
