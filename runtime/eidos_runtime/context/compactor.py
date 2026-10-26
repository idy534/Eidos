from __future__ import annotations

import json
import re
import threading
import time
import uuid
from collections.abc import Iterable
from typing import Callable

from eidos_runtime.context.facts import CompactSummary, ContextFacts, ContextItemFact
from eidos_runtime.context.verified_compaction import CompactionVerificationError
from eidos_runtime.context.tokens import estimate_text_tokens
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.db.errors import InvalidRunStateError
from eidos_runtime.domain.context_management import ContextCompactionRecord
from eidos_runtime.model.client import ModelClient
from eidos_runtime.persistence.context_management import ContextManagementRepository
from eidos_runtime.context.model_summary import AssistedSummary, assist_summary
from eidos_runtime.context.working_set import recent_item_ids
from eidos_runtime.context.summary_evidence import continuity_context, model_source_text
from eidos_runtime.context.input_references import project_input_mentions
from eidos_runtime.persistence.input_context import InputContextRepository
from eidos_runtime.context.policy import CONTINUITY_CONTEXT_MAX_BYTES
from eidos_runtime.model.context_projection import text_preview


class ContextCompactionError(RuntimeError):
    pass


SUMMARY_MAX_ITEMS = 16
SUMMARY_ENTRY_MAX_BYTES = 512
SUMMARY_TASK_GOAL_MAX_BYTES = 1_024
SUMMARY_MAX_SERIALIZED_BYTES = 24 * 1_024
_SYMBOL_PATTERN = re.compile(
    r"(?m)^\s*(?:async\s+)?(?:def|class|fn|struct|enum|interface|type|function|const)\s+"
    r"([A-Za-z_][A-Za-z0-9_]*)"
)
_CONSTRAINT_MARKERS = (
    "must ", "must:", "do not ", "don't ", "only ", "preserve ",
    "keep ", "required", "constraint", "必须", "不要", "仅", "保留",
    "不能", "不得", "禁止", "只做", "只分析", "先不", "确认后", "等我确认",
)
_DECISION_MARKERS = (
    "decided", "chosen", "决定", "采用", "选择",
)
_NEXT_ACTION_MARKERS = (
    "next", "todo", "run ", "inspect ", "verify ", "continue", "下一步", "接下来",
)


class ContextCompactor:
    """Persists a bounded deterministic summary without deleting source history."""

    def __init__(self, store: SessionStore, model: ModelClient | None = None,
                 *, on_recorded: Callable[[], object] | None = None) -> None:
        self.store = store
        self.model = model
        self._assisted: AssistedSummary | None = None
        self._source_count = 0
        self._on_recorded = on_recorded

    def compact(
        self, run_id: str, phase: str, *, keep_recent_items: int = 0,
        cancel: threading.Event | None = None, trigger: str = "window_pressure",
        request_id: str | None = None, measure: Callable[[], int] | None = None,
        before_tokens: int | None = None,
        recent_token_budget: int | None = None,
    ) -> CompactSummary:
        run = self.store.read_run(run_id)
        if run["status"] != "running" or run.get("cancelRequestedAt") is not None:
            raise InvalidRunStateError("run is not active")
        repository = ContextManagementRepository(self.store.database)
        record = ContextCompactionRecord(request_id=request_id or str(uuid.uuid4()),
                                         stage="started", trigger=trigger, status="running",
                                         before_tokens=before_tokens)
        repository.record(run_id, record)
        if self._on_recorded is not None:
            self._on_recorded()
        started = time.monotonic()
        cancel = cancel or threading.Event()
        self._assisted = None
        self._source_count = 0
        status = "failed"
        reason = "compaction_failed"
        after = None

        def outcome_record(status: str, reason: str | None, after: int | None = None) -> ContextCompactionRecord:
            assisted = self._assisted
            usage = assisted.usage if assisted else None
            return record.model_copy(update={
                "stage": "finished", "status": status, "reason": reason,
                "strategy": "model" if assisted and assisted.accepted else "deterministic",
                "duration_ms": round((time.monotonic() - started) * 1000),
                "after_tokens": after, "source_item_count": self._source_count,
                "input_tokens": usage.input_tokens if usage else None,
                "output_tokens": usage.output_tokens if usage else None,
            })

        try:
            if cancel.is_set():
                raise ContextCompactionError("compaction canceled")
            summary = self._compact(
                run_id, phase, keep_recent_items=keep_recent_items, recent_token_budget=recent_token_budget, cancel=cancel,
                record_on_commit=lambda: outcome_record("succeeded", self._assisted.reason if self._assisted else "model_unavailable"),
            )
            status = "succeeded"
            reason = self._assisted.reason if self._assisted else "model_unavailable"
            # Rebuild after commit: this count includes instructions, tools and
            # the surviving recent window, rather than only summary text.
            if measure is not None:
                try:
                    after = measure()
                except Exception:
                    after = None
            return summary
        except (ContextCompactionError, InvalidRunStateError) as error:
            reason = {"no compactable history": "no_compactable_history",
                      "critical summary facts exceed context budget": "critical_facts_limit",
                      "compaction proposal was not verified": "source_changed"}.get(str(error), "compaction_failed")
            if cancel.is_set() or self.store.read_run(run_id).get("cancelRequestedAt") is not None:
                status, reason = "canceled", "canceled"
            raise
        finally:
            repository.record(run_id, outcome_record(status, reason, after))
            if self._on_recorded is not None:
                self._on_recorded()

    def _compact(self, run_id: str, phase: str, *, keep_recent_items: int, recent_token_budget: int | None,
                 cancel: threading.Event, record_on_commit: Callable[[], ContextCompactionRecord]) -> CompactSummary:
        if phase not in {"pre_turn", "mid_turn"}:
            raise ValueError("invalid compaction phase")
        if keep_recent_items < 0:
            raise ValueError("invalid recent item count")
        repository = self.store.verified_compaction_repository()
        guard = ContextManagementRepository(self.store.database).guard(run_id)
        facts = repository.load_facts(run_id)
        existing = facts.compact_summary
        profile = self.store.read_model_profile(run_id)
        eligible = tuple(
            item for item in facts.items
            if item.item_id != facts.current_user_goal_id
            and (phase == "mid_turn" or item.run_id != run_id)
            and (existing is None or item.item_id not in existing.source_item_ids)
        )
        if keep_recent_items:
            # Soft compaction keeps current instructions and the recent working
            # evidence verbatim. Hard window recovery retains its existing path.
            protected = (recent_item_ids(eligible, recent_token_budget) if recent_token_budget is not None
                         else {item.item_id for item in eligible[-keep_recent_items:]})
            groups = {(item.run_id, item.model_step_index) for item in eligible
                      if item.item_id in protected and item.model_step_index is not None}
            protected.update(item.item_id for item in eligible if item.model_step_index is not None
                             and (item.run_id, item.model_step_index) in groups)
            eligible = tuple(
                item for item in eligible
                if item.status in {"completed", "failed"}
                and item.kind != "user_message"
                and item.tool_name not in {"skill_read", "skill_read_resource"}
                and item.item_id not in protected
            )
        if not eligible:
            raise ContextCompactionError("no compactable history")
        source_ids = tuple(dict.fromkeys((
            *(existing.source_item_ids if existing else ()),
            *(item.item_id for item in eligible),
        )))
        self._source_count = len(source_ids)
        source_facts = repository.load_facts(run_id, required_item_ids=source_ids)
        users = [model_source_text(item) for item in eligible if item.kind == "user_message"]
        input_references = tuple(
            f"用户引用：{reference.label}；类型：{reference.kind}；来源：{reference.source}"
            for item in eligible for reference in item.input_references
        )
        assistants = [item.content or "" for item in eligible if item.kind == "assistant_message"]
        tools = [item for item in eligible if item.provider_call_id is not None]
        tool_records = tuple((_tool_text(item), item) for item in tools)
        successful_tools = tuple(
            text for text, item in tool_records if _tool_outcome(item) == "success"
        )
        failed_tools = tuple(
            text for text, item in tool_records if _tool_outcome(item) != "success"
        )
        # Keep unresolved effects as stable references to durable originals.
        # Repeating old error bodies can consume the entire next summary.
        uncertain_tools = tuple(
            _uncertain_tool_text(item) for item in source_facts.items
            if item.provider_call_id is not None and _tool_has_uncertain_side_effects(item)
        )
        workspace_state = _workspace_state(facts)
        user_constraints = tuple(
            fragment for user in users for fragment in _constraint_fragments(user)
        )
        decisions = tuple(
            _bounded("Assistant decision report (not user approval): " + assistant, SUMMARY_ENTRY_MAX_BYTES)
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
            constraints=tuple(dict.fromkeys((
                *(existing.constraints if existing else ()), *user_constraints,
            ))),
            completed_actions=_merge(
                tuple(value for value in existing.completed_actions if value.startswith("Tool completed:")) if existing else (),
                tuple("Tool completed: " + value for value in successful_tools),
            ),
            workspace_changes=_merge(
                existing.workspace_changes if existing else (),
                source_facts.committed_workspace_changes,
            ),
            important_facts=(workspace_state, *_merge(
                tuple(value for value in existing.important_facts if not value.startswith("workspace state:")) if existing else (),
                (*input_references, *(
                    text_preview(
                        f"Historical user request item={item.item_id}: {model_source_text(item)}",
                        SUMMARY_ENTRY_MAX_BYTES,
                        continuation=f"read_tool_output(callId='{item.item_id}', stream='context') reads the saved original.",
                    )
                    for item in eligible if item.kind == "user_message"
                ), *(
                    _bounded(f"Assistant report, unverified, item={item.item_id}: {item.content or ''}", SUMMARY_ENTRY_MAX_BYTES)
                    for item in eligible if item.kind == "assistant_message"
                ), *(
                    value if value.startswith("Historical report, unverified:") else "Historical report, unverified: " + value
                    for value in (existing.completed_actions if existing else ()) if not value.startswith("Tool completed:")
                ), *successful_tools),
            )),
            unresolved_problems=_merge(
                existing.unresolved_problems if existing else (),
                (*failed_tools, *(("workspace result unknown",)
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
            pending_approvals=facts.pending_approval_ids,
            uncertain_side_effects=_merge(
                (),
                (*uncertain_tools, *(
                    ("uncertain side effects may exist",)
                    if facts.side_effects_may_exist else ()
                )),
            ),
            continuity_notes=existing.continuity_notes if existing else (),
        )
        input_budget = profile.context_window_tokens - profile.max_output_tokens
        proposal = _fit_summary(summary, max_tokens=min(4_096, max(512, input_budget // 12)))
        if self.model is not None and hasattr(self.model, "profile_snapshot"):
            retained_ids = {ref.item_id for note in proposal.continuity_notes for ref in note.evidence}
            self._assisted = assist_summary(
                self.model, proposal, eligible, cancel,
                prior_items=tuple(item for item in source_facts.items if item.item_id in retained_ids),
                task_context=project_input_mentions(
                    str(self.store.read_run(run_id).get("userInput") or ""),
                    tuple(snapshot.reference for snapshot in InputContextRepository(self.store.database).for_run(run_id)),
                ),
            )
            if self._assisted.accepted:
                # Do not accept source coverage after silently discarding newly
                # grounded facts just to make the summary smaller.
                proposal = _fit_summary(self._assisted.summary, max_tokens=min(4_096, max(512, input_budget // 12)))
        if cancel.is_set() or self.store.read_run(run_id).get("cancelRequestedAt") is not None:
            raise ContextCompactionError("compaction canceled")
        ordinals = tuple(
            item.ordinal for item in facts.items
            if item.item_id in proposal.source_item_ids
        )
        try:
            verified = repository.verify_and_persist(
                run_id=run_id,
                summary=proposal,
                input_range=(0, max(ordinals, default=0)),
                source_tool_call_ids=source_facts.available_tool_call_ids,
                pending_approval_facts=proposal.pending_approvals,
                reconciliation_facts=(
                    ("workspace reconciliation required",)
                    if facts.reconciliation_required else ()
                ),
                phase=phase,
                expected_guard=guard,
                compaction=record_on_commit(),
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
    if (item.tool_name or "").startswith("memory_"):
        return f"{item.tool_name}: historical memory reference; retrieve current evidence again when needed."
    result = _tool_result(item)
    parts = [
        f"item={item.item_id};call={item.provider_call_id}",
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
            "sizeBytes", "scannedBytes", "exitCode", "termination",
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


def _tool_has_uncertain_side_effects(item: ContextItemFact) -> bool:
    result = _tool_result(item)
    return result.get("sideEffectsMayExist") is True or result.get(
        "reconciliationRequired"
    ) is True or (
        isinstance(result.get("data"), dict)
        and result["data"].get("workspaceChangeState") == "unknown"
    )


def _uncertain_tool_text(item: ContextItemFact) -> str:
    result = _tool_result(item)
    data = result.get("data")
    state = data.get("workspaceChangeState", "unverified") if isinstance(data, dict) else "unverified"
    return (f"Unverified tool effects: item={item.item_id};call={item.provider_call_id};"
            f"tool={item.tool_name};workspace={state};"
            f"sideEffectsMayExist={result.get('sideEffectsMayExist') is True};"
            f"reconciliationRequired={result.get('reconciliationRequired') is True}")


def _workspace_state(facts: ContextFacts) -> str:
    values = [
        f"workspace state: version={facts.workspace_version}",
        f"reconciliationEpoch={facts.reconciliation_epoch}",
    ]
    if facts.reconciliation_required:
        values.append("reconciliationRequired=true")
    if facts.side_effects_may_exist:
        values.append("sideEffectsMayExist=true")
    return _bounded("; ".join(values), SUMMARY_ENTRY_MAX_BYTES)


def _constraint_fragments(value: str) -> tuple[str, ...]:
    fragments = []
    for line in value.splitlines() or [value]:
        if _contains_marker(line, _CONSTRAINT_MARKERS):
            fragments.append(line.strip())
    return tuple(fragments)


def _contains_marker(value: str, markers: Iterable[str]) -> bool:
    lowered = value.lower()
    return any(marker in lowered or marker in value for marker in markers)


def _unique_bounded(values: Iterable[str], limit: int, entry_limit: int) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        _bounded(value, entry_limit) for value in values if value
    ))[:limit]


def _fit_summary(summary: CompactSummary, *, max_tokens: int = 4_096) -> CompactSummary:
    """Keep the summary useful for small context windows as well as large ones."""
    fields = (
        "completed_actions", "important_facts",
        "next_actions", "important_decisions", "failed_attempts",
    )
    candidate = summary
    if len(candidate.continuity_notes) > 128:
        raise ContextCompactionError("critical summary facts exceed context budget")
    while (_summary_size(candidate) > SUMMARY_MAX_SERIALIZED_BYTES
           or len(continuity_context(candidate.continuity_notes).encode("utf-8")) > CONTINUITY_CONTEXT_MAX_BYTES
           or estimate_text_tokens(candidate.model_dump_json(exclude={"source_item_ids"})) > max_tokens):
        for field in fields:
            values = getattr(candidate, field)
            if len(values) > 1:
                # Keep the newest reports/actions rather than discarding them
                # first. Control facts and explicit constraints are not trimmed.
                if field == "important_facts":
                    removable = next((i for i, value in enumerate(values[1:], 1)
                                      if not _protected_legacy_fact(value)), None)
                    if removable is None:
                        continue
                    remaining = (*values[:removable], *values[removable + 1:])
                else:
                    remaining = values[1:]
                candidate = candidate.model_copy(update={field: remaining})
                break
        else:
            raise ContextCompactionError("critical summary facts exceed context budget")
    return candidate


def _summary_size(summary: CompactSummary) -> int:
    return len(json.dumps(
        summary.model_dump(mode="json", exclude={"source_item_ids"}),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8"))


def _protected_legacy_fact(value: str) -> bool:
    return value.startswith("Model note (")


def _merge(existing: tuple[str, ...], values) -> tuple[str, ...]:
    merged = tuple(dict.fromkeys(
        str(value) if _protected_legacy_fact(str(value)) else _bounded(str(value), SUMMARY_ENTRY_MAX_BYTES)
        for value in (*existing, *values)
        if value
    ))
    protected = tuple(value for value in merged if _protected_legacy_fact(value))
    ordinary = tuple(value for value in merged if not _protected_legacy_fact(value))
    if len(ordinary) <= SUMMARY_MAX_ITEMS:
        return (*protected, *ordinary)
    preserved = SUMMARY_MAX_ITEMS // 4
    return (*protected, *ordinary[:preserved], *ordinary[-(SUMMARY_MAX_ITEMS - preserved):])


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
