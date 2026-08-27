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
    # Chat Completions does not provide an assistant message phase. Keep the
    # classification useful for tool-bearing responses, but leave all other
    # responses unknown so the Runtime can decide completion from normalized
    # follow-up state instead of inferring it from provider metadata.
    if has_tool_calls:
        return AssistantMessagePhase.COMMENTARY
    return AssistantMessagePhase.UNKNOWN
