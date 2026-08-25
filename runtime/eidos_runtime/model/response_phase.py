from __future__ import annotations

from enum import StrEnum


class AssistantMessagePhase(StrEnum):
    COMMENTARY = "commentary"
    FINAL_ANSWER = "final_answer"
    UNKNOWN = "unknown"


def resolve_chat_completion_phase(
    *,
    text: str,
    has_tool_calls: bool,
    finish_reason: str | None,
) -> AssistantMessagePhase:
    if has_tool_calls:
        return AssistantMessagePhase.COMMENTARY
    if text and finish_reason == "stop":
        return AssistantMessagePhase.FINAL_ANSWER
    return AssistantMessagePhase.UNKNOWN
