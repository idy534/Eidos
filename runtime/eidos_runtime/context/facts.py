from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _Fact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class ContextItemFact(_Fact):
    item_id: str
    run_id: str
    kind: str
    status: str
    content: str | None = None
    provider_call_id: str | None = None
    tool_name: str | None = None
    arguments_json: str | None = None
    result_json: str | None = None


class CompactSummary(_Fact):
    task_goal: str
    constraints: tuple[str, ...]
    completed_actions: tuple[str, ...]
    workspace_changes: tuple[str, ...]
    important_facts: tuple[str, ...]
    unresolved_problems: tuple[str, ...]
    next_actions: tuple[str, ...]
    source_item_ids: tuple[str, ...]


class ContextFacts(_Fact):
    run_id: str
    session_id: str
    items: tuple[ContextItemFact, ...]
    compact_summary: CompactSummary | None = None
    compaction_count: int = 0
    workspace_version: int = 0
    reconciliation_epoch: int = 0
    last_diff_hash: str | None = None
    candidate_overflow: bool = False
    current_user_goal_id: str | None = None
