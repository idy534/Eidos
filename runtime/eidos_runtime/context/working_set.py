from __future__ import annotations

from eidos_runtime.context.facts import ContextItemFact
from eidos_runtime.context.tokens import estimate_text_tokens


def recent_item_ids(items: tuple[ContextItemFact, ...], token_budget: int) -> set[str]:
    """Retain whole recent model responses under a reference-token budget.

    The latest response is always protected, including a response larger than
    the target. Clearing must never split tool/reasoning replay combinations.
    """
    groups: dict[tuple[str, object], list[ContextItemFact]] = {}
    for item in items:
        key = (item.run_id, item.model_step_index if item.model_step_index is not None else item.item_id)
        groups.setdefault(key, []).append(item)
    protected: set[str] = set()
    used = 0
    for group in reversed(tuple(groups.values())):
        cost = sum(estimate_text_tokens((item.content or "") + (item.arguments_json or "")
                                       + (item.model_result_json or item.result_json or "")) + 32 for item in group)
        if protected and used + cost > token_budget:
            break
        protected.update(item.item_id for item in group)
        used += cost
    return protected

