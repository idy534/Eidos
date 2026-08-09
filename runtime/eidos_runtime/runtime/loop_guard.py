from __future__ import annotations

import hashlib
import json

from eidos_runtime.context.facts import ContextFacts
from eidos_runtime.model.client import ModelToolCall
from eidos_runtime.runtime.contracts import LoopStateFingerprint, ProgressSignature


class LoopGuard:
    """Detect semantic state convergence without task-length counters."""

    def __init__(self) -> None:
        self._last_progress: ProgressSignature | None = None
        self._seen_successes: set[str] = set()
        self._seen_facts: set[str] = set()
        self._seen_user_inputs: set[str] = set()
        self._active_errors: set[str] = set()
        self._observed_states: set[str] = set()
        self._recovered_states: set[str] = set()
        self._last_loop_state: str | None = None
        self._empty_responses = 0

    @classmethod
    def from_signatures(
        cls, signatures: tuple[ProgressSignature, ...]
    ) -> "LoopGuard":
        guard = cls()
        for signature in signatures:
            guard.observe_progress(signature)
            if signature.recovery_state_fingerprint is not None:
                guard._recovered_states.add(signature.recovery_state_fingerprint)
        return guard

    def make_signature(
        self,
        *,
        workspace_version: int,
        diff_hash: str | None,
        successful_tool_result_hashes: tuple[str, ...],
        context_fact_ids: tuple[str, ...],
        error_fingerprints: tuple[str, ...],
        reconciliation_epoch: int,
        new_user_input_ids: tuple[str, ...] = (),
        tool_call_fingerprint: str | None = None,
        loop_state_fingerprint: str | None = None,
        recovery_state_fingerprint: str | None = None,
    ) -> ProgressSignature:
        unique_errors = tuple(sorted(set(error_fingerprints)))
        return ProgressSignature(
            workspace_version=workspace_version,
            diff_hash=diff_hash,
            successful_tool_result_hashes=tuple(
                value for value in successful_tool_result_hashes
                if value not in self._seen_successes
            ),
            new_context_fact_ids=tuple(
                value for value in context_fact_ids if value not in self._seen_facts
            ),
            error_fingerprints=unique_errors,
            resolved_error_fingerprints=tuple(sorted(
                self._active_errors - set(unique_errors)
            )),
            reconciliation_epoch=reconciliation_epoch,
            new_user_input_ids=tuple(
                value for value in new_user_input_ids
                if value not in self._seen_user_inputs
            ),
            tool_call_fingerprint=tool_call_fingerprint,
            loop_state_fingerprint=loop_state_fingerprint,
            recovery_state_fingerprint=recovery_state_fingerprint,
        )

    def observe_tool_calls(
        self,
        calls: tuple[ModelToolCall, ...],
        workspace_version: int,
        reconciliation_epoch: int,
        *,
        context_fact_frontier_hash: str,
        active_error_fingerprints: tuple[str, ...] = (),
    ) -> str | None:
        fingerprint = loop_state_fingerprint(
            calls,
            workspace_version,
            reconciliation_epoch,
            context_fact_frontier_hash=context_fact_frontier_hash,
            active_error_fingerprints=active_error_fingerprints,
        )
        self._last_loop_state = fingerprint
        if fingerprint in self._recovered_states:
            return "repeated_tool_call"
        if fingerprint in self._observed_states:
            return "recover_repeated_tool_call"
        return None

    def record_tool_result_state(
        self,
        calls: tuple[ModelToolCall, ...],
        workspace_version: int,
        reconciliation_epoch: int,
        *,
        context_fact_frontier_hash: str,
        active_error_fingerprints: tuple[str, ...] = (),
    ) -> str:
        fingerprint = loop_state_fingerprint(
            calls,
            workspace_version,
            reconciliation_epoch,
            context_fact_frontier_hash=context_fact_frontier_hash,
            active_error_fingerprints=active_error_fingerprints,
        )
        self._observed_states.add(fingerprint)
        self._last_loop_state = fingerprint
        return fingerprint

    def mark_recovery_attempted(self) -> str:
        if self._last_loop_state is None:
            raise RuntimeError("no loop state is available for recovery")
        self._recovered_states.add(self._last_loop_state)
        return self._last_loop_state

    def observe_progress(self, signature: ProgressSignature) -> str | None:
        unchanged = (
            self._last_progress is None
            or (
                signature.workspace_version == self._last_progress.workspace_version
                and signature.diff_hash == self._last_progress.diff_hash
                and signature.reconciliation_epoch
                == self._last_progress.reconciliation_epoch
            )
        )
        progressed = bool(
            not unchanged
            or signature.successful_tool_result_hashes
            or signature.new_context_fact_ids
            or signature.resolved_error_fingerprints
            or signature.new_user_input_ids
        )
        self._last_progress = signature
        self._seen_successes.update(signature.successful_tool_result_hashes)
        self._seen_facts.update(signature.new_context_fact_ids)
        self._seen_user_inputs.update(signature.new_user_input_ids)
        self._active_errors = set(signature.error_fingerprints)
        if signature.loop_state_fingerprint is not None:
            self._observed_states.add(signature.loop_state_fingerprint)
        state = _progress_state_fingerprint(
            workspace_version=signature.workspace_version,
            diff_hash=signature.diff_hash,
            reconciliation_epoch=signature.reconciliation_epoch,
            active_error_fingerprints=signature.error_fingerprints,
            successful_tool_result_hashes=self._seen_successes,
            context_fact_ids=self._seen_facts,
            user_input_ids=self._seen_user_inputs,
        )
        self._last_loop_state = state
        if signature.recovery_state_fingerprint is not None:
            self._recovered_states.add(signature.recovery_state_fingerprint)
        if progressed:
            self._observed_states.add(state)
            return None
        if state in self._recovered_states:
            return "no_progress"
        if state in self._observed_states:
            return "recover_no_progress"
        self._observed_states.add(state)
        return None

    def observe_empty_response(self, empty: bool) -> str | None:
        self._empty_responses = self._empty_responses + 1 if empty else 0
        return "repeated_empty_response" if self._empty_responses >= 2 else None

def tool_call_fingerprint(
    calls: tuple[ModelToolCall, ...],
) -> str:
    return _hash([
        {
            "toolName": call.name,
            "canonicalArguments": call.arguments,
        }
        for call in calls
    ])


def loop_state_fingerprint(
    calls: tuple[ModelToolCall, ...],
    workspace_version: int,
    reconciliation_epoch: int,
    *,
    context_fact_frontier_hash: str,
    active_error_fingerprints: tuple[str, ...] = (),
) -> str:
    state = LoopStateFingerprint(
        tool_call_fingerprint=tool_call_fingerprint(calls),
        workspace_version=workspace_version,
        reconciliation_epoch=reconciliation_epoch,
        active_error_fingerprints=tuple(sorted(set(active_error_fingerprints))),
        context_fact_frontier_hash=context_fact_frontier_hash,
    )
    return _hash({"kind": "tool", **state.model_dump(mode="json")})


def context_fact_frontier_hash(facts: ContextFacts) -> str:
    semantic_facts = {
        _canonical_json({
            "kind": item.kind,
            "status": item.status,
            "content": item.content,
            "toolName": item.tool_name,
            "arguments": _json_value(item.arguments_json),
            "result": _json_value(item.model_result_json or item.result_json),
        })
        for item in facts.items
    }
    return _hash({
        "facts": sorted(semantic_facts),
        "compactSummary": (
            facts.compact_summary.model_dump(mode="json")
            if facts.compact_summary is not None else None
        ),
    })


def _progress_state_fingerprint(
    *,
    workspace_version: int,
    diff_hash: str | None,
    reconciliation_epoch: int,
    active_error_fingerprints: tuple[str, ...],
    successful_tool_result_hashes: set[str],
    context_fact_ids: set[str],
    user_input_ids: set[str],
) -> str:
    return _hash({
        "kind": "progress",
        "workspaceVersion": workspace_version,
        "diffHash": diff_hash,
        "reconciliationEpoch": reconciliation_epoch,
        "activeErrors": sorted(set(active_error_fingerprints)),
        "successfulResults": sorted(successful_tool_result_hashes),
        "contextFacts": sorted(context_fact_ids),
        "userInputs": sorted(user_input_ids),
    })


def _json_value(value: str | None) -> object:
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _canonical_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def _hash(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
