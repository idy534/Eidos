"""Runtime-authored text inside user turns, kept apart from what the user typed.

Providers have no portable role for background context, so Eidos sends it in
the user role. Every Runtime-authored block is wrapped in ``<system-reminder>``;
text outside those blocks is the user's own input. A literal tag inside any
text is neutralized so quoted or pasted material cannot open or close a block.
"""
from __future__ import annotations

from collections.abc import Sequence
import re
from typing import Literal, TypeVar

from eidos_runtime.model.client import ModelContextItem


REMINDER_OPEN = "<system-reminder>"
REMINDER_CLOSE = "</system-reminder>"
_REMINDER_TAG = re.compile(r"<(?=\s*/?\s*system-reminder\b)", re.IGNORECASE)

_Media = TypeVar("_Media")

# human: the user's own text. reference: a reference the user attached to the
# preceding human text. reminder: any other Runtime-authored user-role text.
TurnKind = Literal["human", "reference", "reminder"]


def neutralize_reminder_tags(text: str) -> str:
    return _REMINDER_TAG.sub("&lt;", text)


def render_reminder(text: str) -> str:
    body = neutralize_reminder_tags(text.strip("\n"))
    return f"{REMINDER_OPEN}\n{body}\n{REMINDER_CLOSE}"


def reminder_content(content: str | Sequence[str | _Media]) -> str | list[str | _Media]:
    """Wrap Runtime text and keep its attached media in the same turn."""
    if isinstance(content, str):
        return render_reminder(content)
    text = "\n".join(part for part in content if isinstance(part, str))
    return [render_reminder(text), *(part for part in content if not isinstance(part, str))]


def is_runtime_context(item: ModelContextItem) -> bool:
    """Context records, plus older snapshots that stored them as sectioned user items."""
    return item.get("type") == "context" or (
        item.get("type", item.get("role")) == "user" and isinstance(item.get("sectionId"), str)
    )


def user_turn_text(item: ModelContextItem, content: str) -> str:
    return render_reminder(content) if is_runtime_context(item) else neutralize_reminder_tags(content)


def user_turn_kind(item: ModelContextItem) -> TurnKind:
    if not is_runtime_context(item):
        return "human"
    return "reference" if str(item.get("sectionId", "")).startswith("input-reference:") else "reminder"


def plan_user_turns(kinds: Sequence[TurnKind]) -> list[list[int]]:
    """Order and group adjacent user-role messages into provider messages.

    The user's own text is always a message of its own. Adjacent reminders form
    one message. A reference moves ahead of the human text it belongs to, so the
    material comes before the request and the request stays last.
    """
    order: list[int] = []
    human_position: int | None = None
    for index, kind in enumerate(kinds):
        if kind == "reference" and human_position is not None:
            order.insert(human_position, index)
            human_position += 1
        else:
            order.append(index)
            human_position = len(order) - 1 if kind == "human" else None
    groups: list[list[int]] = []
    for index in order:
        if kinds[index] != "human" and groups and kinds[groups[-1][0]] != "human":
            groups[-1].append(index)
        else:
            groups.append([index])
    return groups


def join_user_content(
    *contents: str | Sequence[str | _Media],
) -> str | list[str | _Media]:
    """Concatenate adjacent user-turn text; media keeps its position."""
    parts: list[str | _Media] = []
    for content in contents:
        for part in (content,) if isinstance(content, str) else content:
            if isinstance(part, str) and parts and isinstance(parts[-1], str):
                parts[-1] = parts[-1] + "\n\n" + part
            else:
                parts.append(part)
    if len(parts) == 1 and isinstance(parts[0], str):
        return parts[0]
    return parts
