from __future__ import annotations

import json
import re
from collections.abc import Iterable

from eidos_runtime.context.facts import CompactSummary, ContextFacts, ContextItemFact
from eidos_runtime.context.verified_compaction import CompactionVerificationError
from eidos_runtime.db.storage import SessionStore


class ContextCompactionError(RuntimeError):
    pass


SUMMARY_MAX_ITEMS = 16
SUMMARY_ENTRY_MAX_BYTES = 512
SUMMARY_TASK_GOAL_MAX_BYTES = 1_024
SUMMARY_MAX_SERIALIZED_BYTES = 4 * 1_024
_SYMBOL_PATTERN = re.compile(
    r"(?m)^\s*(?:async\s+)?(?:def|class|fn|struct|enum|interface|type|function|const)\s+"
    r"([A-Za-z_][A-Za-z0-9_]*)"
)
_CONSTRAINT_MARKERS = (
    "must ", "must:", "do not ", "don't ", "only ", "preserve ",
    "keep ", "required", "constraint", "必须", "不要", "仅", "保留",
)
_DECISION_MARKERS = (
    "decided", "we will", "i will", "use ", "chosen", "决定", "采用", "选择",
)
_NEXT_ACTION_MARKERS = (
    "next", "todo", "run ", "inspect ", "verify ", "continue", "下一步", "接下来",
)


class ContextCompactor:
    """Persists a bounded deterministic summary without deleting source history."""

    def __init__(self, store: SessionStore) -> None:
        self.store = store

    def compact(self, run_id: str, phase: str) -> CompactSummary:
        if phase not in {"pre_turn", "mid_turn"}:
            raise ValueError("invalid compaction phase")
        repository = self.store.verified_compaction_repository()
        facts = repository.load_facts(run_id)
        existing = facts.compact_summary
        eligible = tuple(
            item for item in facts.items
            if item.item_id != facts.current_user_goal_id
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
        tool_records = tuple((_tool_text(item), item) for item in tools)
        successful_tools = tuple(
            text for text, item in tool_records if _tool_outcome(item) == "success"
        )
        failed_tools = tuple(
            text for text, item in tool_records if _tool_outcome(item) != "success"
        )
        uncertain_tools = tuple(
            text for text, item in tool_records if _tool_has_uncertain_side_effects(item)
        )
        workspace_state = _workspace_state(facts)
        user_constraints = tuple(
            fragment for user in users for fragment in _constraint_fragments(user)
        )
        decisions = tuple(
            _bounded(assistant, SUMMARY_ENTRY_MAX_BYTES)
            for assistant in assistants
            if _contains_marker(assistant, _DECISION_MARKERS)
        )
        next_actions = tuple(
            _bounded(assistant, SUMMARY_ENTRY_MAX_BYTES)
            for assistant in assistants
            if _contains_marker(assistant, _NEXT_ACTION_MARKERS)
        ) or tuple(users[-1:])
        summary = CompactSummary(
            task_goal=_bounded(
                existing.task_goal if existing else (users[0] if users else "Continue the task"),
                SUMMARY_TASK_GOAL_MAX_BYTES,
            ),
            constraints=_merge(
                existing.constraints if existing else (),
                (*user_constraints, *users[1:]),
            ),
            completed_actions=_merge(existing.completed_actions if existing else (), assistants),
            workspace_changes=_merge(
                existing.workspace_changes if existing else (),
                facts.committed_workspace_changes,
            ),
            important_facts=_merge(
                existing.important_facts if existing else (),
                (workspace_state, *successful_tools),
            ),
            unresolved_problems=_merge(
                existing.unresolved_problems if existing else (),
                (*failed_tools, *(
                    f"active runtime error: {value}"
                    for value in facts.active_error_fingerprints
                ), *(("workspace reconciliation required",)
                     if facts.reconciliation_required else ())),
            ),
            next_actions=_merge(existing.next_actions if existing else (), next_actions),
            source_item_ids=source_ids,
            important_decisions=_merge(
                existing.important_decisions if existing else (), decisions
            ),
            failed_attempts=_merge(
                existing.failed_attempts if existing else (), failed_tools
            ),
            pending_approvals=_merge(
                existing.pending_approvals if existing else (),
                facts.pending_approval_ids,
            ),
            uncertain_side_effects=_merge(
                existing.uncertain_side_effects if existing else (),
                (*uncertain_tools, *(
                    ("uncertain side effects may exist",)
                    if facts.side_effects_may_exist else ()
                )),
            ),
        )
        proposal = _fit_summary(summary)
        ordinals = tuple(
            item.ordinal for item in facts.items
            if item.item_id in proposal.source_item_ids
        )
        try:
            verified = repository.verify_and_persist(
                run_id=run_id,
                summary=proposal,
                input_range=(0, max(ordinals, default=0)),
                source_tool_call_ids=facts.available_tool_call_ids,
                pending_approval_facts=proposal.pending_approvals,
                reconciliation_facts=(
                    ("workspace reconciliation required",)
                    if facts.reconciliation_required else ()
                ),
                phase=phase,
            )
        except CompactionVerificationError as error:
            raise ContextCompactionError(
                "compaction proposal was not verified"
            ) from error
        return verified.summary


def _tool_outcome(item: ContextItemFact) -> object:
    value = _tool_result(item)
    return value.get("outcome")


def _tool_result(item: ContextItemFact) -> dict[str, object]:
    try:
        value = json.loads(item.model_result_json or item.result_json or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _tool_text(item: ContextItemFact) -> str:
    result = _tool_result(item)
    parts = [
        item.tool_name or "tool",
        f"outcome={result.get('outcome', 'unknown')}",
    ]
    if code := result.get("code"):
        parts.append(f"code={code}")
    if summary := result.get("summary"):
        parts.append(f"summary={_bounded(str(summary), 240)}")
    data = result.get("data")
    if isinstance(data, dict):
        paths = _paths(data)
        if paths:
            parts.append(f"paths={','.join(paths)}")
        symbols = _symbols(data)
        if symbols:
            parts.append(f"symbols={','.join(symbols)}")
        matches = _matches(data)
        if matches:
            parts.append(f"matches={'; '.join(matches)}")
        for key in (
            "sha256", "sizeBytes", "scannedBytes", "exitCode", "termination",
            "workspaceChanged", "workspaceChangeState", "truncated", "truncationReason",
        ):
            value = data.get(key)
            if value is not None:
                parts.append(f"{key}={_bounded(str(value), 120)}")
        for key in ("stdout", "stderr"):
            value = data.get(key)
            if isinstance(value, str) and value:
                parts.append(f"{key}={_bounded(value, 240)}")
    return _bounded("; ".join(parts), SUMMARY_ENTRY_MAX_BYTES)


def _paths(data: dict[str, object]) -> tuple[str, ...]:
    values: list[str] = []
    path = data.get("path")
    if isinstance(path, str):
        values.append(path)
    paths = data.get("paths")
    if isinstance(paths, list):
        values.extend(value for value in paths if isinstance(value, str))
    matches = data.get("matches")
    if isinstance(matches, list):
        values.extend(
            str(match["path"])
            for match in matches
            if isinstance(match, dict) and isinstance(match.get("path"), str)
        )
    return _unique_bounded(values, 8, 220)


def _symbols(data: dict[str, object]) -> tuple[str, ...]:
    values: list[str] = []
    for key in ("symbol", "symbols"):
        value = data.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(item for item in value if isinstance(item, str))
    content = data.get("content")
    if isinstance(content, str):
        values.extend(_SYMBOL_PATTERN.findall(content[:64 * 1024]))
    return _unique_bounded(values, 8, 160)


def _matches(data: dict[str, object]) -> tuple[str, ...]:
    matches = data.get("matches")
    if not isinstance(matches, list):
        return ()
    values: list[str] = []
    for match in matches[:8]:
        if not isinstance(match, dict):
            continue
        path = match.get("path")
        line = match.get("line")
        preview = match.get("preview")
        if isinstance(path, str) and isinstance(line, int):
            values.append(
                _bounded(
                    f"{path}:{line}:{preview}" if isinstance(preview, str)
                    else f"{path}:{line}",
                    220,
                )
            )
    return tuple(values)


def _tool_changes_workspace(item: ContextItemFact) -> bool:
    result = _tool_result(item)
    if item.tool_name in {"write_file", "apply_patch", "delete_file"}:
        return result.get("outcome") == "success"
    if item.tool_name == "run_shell" and result.get("outcome") != "success":
        return False
    if item.tool_name == "run_shell":
        data = result.get("data")
        return isinstance(data, dict) and (
            data.get("workspaceChanged") is True
            or data.get("workspaceChangeState") in {"changed", "unknown"}
        )
    if result.get("sideEffectsMayExist") is True or result.get(
        "reconciliationRequired"
    ) is True:
        return True
    data = result.get("data")
    return isinstance(data, dict) and (
        data.get("workspaceChanged") is True
        or data.get("workspaceChangeState") in {"changed", "unknown"}
    )


def _tool_has_uncertain_side_effects(item: ContextItemFact) -> bool:
    result = _tool_result(item)
    return result.get("sideEffectsMayExist") is True or result.get(
        "reconciliationRequired"
    ) is True or (
        isinstance(result.get("data"), dict)
        and result["data"].get("workspaceChangeState") == "unknown"
    )


def _workspace_state(facts: ContextFacts) -> str:
    values = [
        f"workspace state: version={facts.workspace_version}",
        f"reconciliationEpoch={facts.reconciliation_epoch}",
    ]
    if facts.last_diff_hash:
        values.append(f"diffHash={facts.last_diff_hash}")
    if facts.reconciliation_required:
        values.append("reconciliationRequired=true")
    if facts.side_effects_may_exist:
        values.append("sideEffectsMayExist=true")
    return _bounded("; ".join(values), SUMMARY_ENTRY_MAX_BYTES)


def _constraint_fragments(value: str) -> tuple[str, ...]:
    fragments = []
    for line in value.splitlines() or [value]:
        if _contains_marker(line, _CONSTRAINT_MARKERS):
            fragments.append(_bounded(line.strip(), SUMMARY_ENTRY_MAX_BYTES))
    return tuple(fragments)


def _contains_marker(value: str, markers: Iterable[str]) -> bool:
    lowered = value.lower()
    return any(marker in lowered or marker in value for marker in markers)


def _unique_bounded(values: Iterable[str], limit: int, entry_limit: int) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        _bounded(value, entry_limit) for value in values if value
    ))[:limit]


def _fit_summary(summary: CompactSummary) -> CompactSummary:
    """Keep the summary useful for small context windows as well as large ones."""
    fields = (
        "completed_actions", "important_facts", "workspace_changes",
        "unresolved_problems", "next_actions", "important_decisions",
        "failed_attempts", "uncertain_side_effects", "pending_approvals",
        "constraints",
    )
    candidate = summary
    while _summary_size(candidate) > SUMMARY_MAX_SERIALIZED_BYTES:
        for field in fields:
            values = getattr(candidate, field)
            if len(values) > 1:
                candidate = candidate.model_copy(update={field: values[:-1]})
                break
        else:
            break
    return candidate


def _summary_size(summary: CompactSummary) -> int:
    return len(json.dumps(
        summary.model_dump(mode="json", exclude={"source_item_ids"}),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8"))


def _merge(existing: tuple[str, ...], values) -> tuple[str, ...]:
    merged = tuple(dict.fromkeys(
        _bounded(str(value), SUMMARY_ENTRY_MAX_BYTES)
        for value in (*existing, *values)
        if value
    ))
    if len(merged) <= SUMMARY_MAX_ITEMS:
        return merged
    preserved = SUMMARY_MAX_ITEMS // 4
    return (*merged[:preserved], *merged[-(SUMMARY_MAX_ITEMS - preserved):])


def _bounded(value: str, limit: int = SUMMARY_ENTRY_MAX_BYTES) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    marker = " …[truncated]"
    prefix = encoded[: max(0, limit - len(marker.encode("utf-8")))]
    while True:
        try:
            return prefix.decode("utf-8") + marker
        except UnicodeDecodeError as error:
            prefix = prefix[:error.start]
