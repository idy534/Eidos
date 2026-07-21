from __future__ import annotations

import json

from eidos_runtime.context.facts import CompactSummary, ContextItemFact
from eidos_runtime.db.storage import SessionStore


class ContextCompactionError(RuntimeError):
    pass


class ContextCompactor:
    """Persists a bounded deterministic summary without deleting source history."""

    def __init__(self, store: SessionStore) -> None:
        self.store = store

    def compact(self, run_id: str, phase: str) -> CompactSummary:
        if phase not in {"pre_turn", "mid_turn"}:
            raise ValueError("invalid compaction phase")
        facts = self.store.context_facts(run_id)
        existing = facts.compact_summary
        latest_user_id = next(
            (item.item_id for item in reversed(facts.items) if item.kind == "user_message"),
            None,
        )
        eligible = tuple(
            item for item in facts.items
            if item.item_id != latest_user_id
            and item.item_id != facts.current_user_goal_id
            and (phase == "mid_turn" or item.run_id != run_id)
            and (existing is None or item.item_id not in existing.source_item_ids)
        )
        if not eligible:
            raise ContextCompactionError("no compactable history")
        source_ids = tuple(dict.fromkeys((
            *(existing.source_item_ids if existing else ()),
            *(item.item_id for item in eligible),
        )))
        users = [item.content or "" for item in eligible if item.kind == "user_message"]
        assistants = [item.content or "" for item in eligible if item.kind == "assistant_message"]
        tools = [item for item in eligible if item.provider_call_id is not None]
        summary = CompactSummary(
            task_goal=_bounded(
                existing.task_goal if existing else (users[0] if users else "Continue the task")
            ),
            constraints=_merge(existing.constraints if existing else (), users[1:]),
            completed_actions=_merge(existing.completed_actions if existing else (), assistants),
            workspace_changes=_merge(
                existing.workspace_changes if existing else (),
                (_tool_text(item) for item in tools if item.tool_name in {
                    "write_file", "apply_patch", "delete_file", "run_shell"
                }),
            ),
            important_facts=_merge(
                existing.important_facts if existing else (),
                (_tool_text(item) for item in tools if _tool_outcome(item) == "success"),
            ),
            unresolved_problems=_merge(
                existing.unresolved_problems if existing else (),
                (_tool_text(item) for item in tools if _tool_outcome(item) != "success"),
            ),
            next_actions=_merge(existing.next_actions if existing else (), users[-1:]),
            source_item_ids=source_ids,
        )
        return self.store.commit_compaction(run_id, phase, summary).value


def _tool_outcome(item: ContextItemFact) -> object:
    try:
        value = json.loads(item.result_json or "{}")
    except json.JSONDecodeError:
        return None
    return value.get("outcome") if isinstance(value, dict) else None


def _tool_text(item: ContextItemFact) -> str:
    return _bounded(f"{item.tool_name}: {item.result_json or '{}'}", 256)


def _merge(existing: tuple[str, ...], values) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*existing, *(_bounded(str(value), 256) for value in values))))[-8:]


def _bounded(value: str, limit: int = 512) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    prefix = encoded[:limit]
    while True:
        try:
            return prefix.decode("utf-8")
        except UnicodeDecodeError as error:
            prefix = prefix[:error.start]
