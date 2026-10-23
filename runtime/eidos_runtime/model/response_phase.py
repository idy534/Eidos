from __future__ import annotations

from enum import StrEnum
from collections.abc import Iterable
from typing import Literal


class AssistantMessagePhase(StrEnum):
    COMMENTARY = "commentary"
    FINAL_ANSWER = "final_answer"
    UNKNOWN = "unknown"


ResponsePhaseSource = Literal["provider", "tool_calls", "unknown"]


def resolve_response_phase(
    native_phases: Iterable[object], *, has_tool_calls: bool,
) -> tuple[AssistantMessagePhase, ResponsePhaseSource]:
    """Keep native phases; a flattened mixed response has no single phase."""
    values = tuple(value for value in native_phases if value is not None)
    known = {AssistantMessagePhase.COMMENTARY.value, AssistantMessagePhase.FINAL_ANSWER.value}
    if values:
        if all(isinstance(value, str) and value in known for value in values) and len(set(values)) == 1:
            return AssistantMessagePhase(values[0]), "provider"
        return AssistantMessagePhase.UNKNOWN, "unknown"
    if has_tool_calls:
        return AssistantMessagePhase.COMMENTARY, "tool_calls"
    return AssistantMessagePhase.UNKNOWN, "unknown"


def validate_end_turn(value: object) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    raise ValueError("invalid_end_turn")
