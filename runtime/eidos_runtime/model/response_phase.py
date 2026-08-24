from __future__ import annotations

from enum import StrEnum


FINAL_RESPONSE_MARKER = "<!-- eidos-final-response -->"


class AssistantMessagePhase(StrEnum):
    COMMENTARY = "commentary"
    FINAL_ANSWER = "final_answer"
    UNKNOWN = "unknown"


def normalize_chat_completion_text(
    text: str,
    *,
    has_tool_calls: bool,
) -> tuple[str, AssistantMessagePhase]:
    cleaned, final_response_declared = consume_final_response_marker(text)
    if has_tool_calls:
        return cleaned, AssistantMessagePhase.COMMENTARY
    if final_response_declared:
        return cleaned, AssistantMessagePhase.FINAL_ANSWER
    return cleaned, AssistantMessagePhase.UNKNOWN


def consume_final_response_marker(text: str) -> tuple[str, bool]:
    stripped = text.rstrip()
    if not stripped.endswith(FINAL_RESPONSE_MARKER):
        return text, False
    return stripped[: -len(FINAL_RESPONSE_MARKER)].rstrip(), True
