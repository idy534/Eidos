from __future__ import annotations

import hashlib
import json

from eidos_runtime.model.client import ModelToolCall
from eidos_runtime.runtime.contracts import ProgressSignature


class LoopGuard:
    def __init__(
        self,
        *,
        max_same_tool_fingerprint: int = 3,
        max_same_error_fingerprint: int = 3,
        max_no_progress_rounds: int = 3,
        max_compactions_per_run: int = 2,
    ) -> None:
        self.max_same_tool_fingerprint = max_same_tool_fingerprint
        self.max_same_error_fingerprint = max_same_error_fingerprint
        self.max_no_progress_rounds = max_no_progress_rounds
        self.max_compactions_per_run = max_compactions_per_run
        self._last_tool: str | None = None
        self._same_tool = 0
        self._last_error: tuple[str, ...] | None = None
        self._same_error = 0
        self._last_progress: ProgressSignature | None = None
        self._no_progress = 0
        self._seen_successes: set[str] = set()
        self._seen_facts: set[str] = set()
        self._active_errors: set[str] = set()
        self._empty_responses = 0
        self._protocol_errors = 0
        self._post_compaction_overflows = 0

    @classmethod
    def from_signatures(
        cls, signatures: tuple[ProgressSignature, ...]
    ) -> "LoopGuard":
        guard = cls()
        for signature in signatures:
            if signature.tool_call_fingerprint is not None:
                guard._same_tool = (
                    guard._same_tool + 1
                    if signature.tool_call_fingerprint == guard._last_tool
                    else 1
                )
                guard._last_tool = signature.tool_call_fingerprint
            guard.observe_progress(signature)
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
            new_user_input_ids=new_user_input_ids,
            tool_call_fingerprint=tool_call_fingerprint,
        )

    def observe_tool_calls(
        self,
        calls: tuple[ModelToolCall, ...],
        workspace_version: int,
        reconciliation_epoch: int,
    ) -> str | None:
        fingerprint = tool_call_fingerprint(
            calls, workspace_version, reconciliation_epoch
        )
        self._same_tool = self._same_tool + 1 if fingerprint == self._last_tool else 1
        self._last_tool = fingerprint
        return (
            "repeated_tool_call"
            if self._same_tool >= self.max_same_tool_fingerprint
            else None
        )

    def observe_errors(self, errors: tuple[str, ...]) -> str | None:
        signature = tuple(sorted(set(errors)))
        if not signature:
            self._last_error = None
            self._same_error = 0
            return None
        self._same_error = self._same_error + 1 if signature == self._last_error else 1
        self._last_error = signature
        return (
            "repeated_tool_error"
            if self._same_error >= self.max_same_error_fingerprint
            else None
        )

    def observe_progress(self, signature: ProgressSignature) -> str | None:
        error_reason = self.observe_errors(signature.error_fingerprints)
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
        self._no_progress = 0 if progressed else self._no_progress + 1
        self._last_progress = signature
        self._seen_successes.update(signature.successful_tool_result_hashes)
        self._seen_facts.update(signature.new_context_fact_ids)
        self._active_errors = set(signature.error_fingerprints)
        if error_reason is not None:
            return error_reason
        return "no_progress" if self._no_progress >= self.max_no_progress_rounds else None

    def observe_empty_response(self, empty: bool) -> str | None:
        self._empty_responses = self._empty_responses + 1 if empty else 0
        return "repeated_empty_response" if self._empty_responses >= 2 else None

    def observe_protocol_error(self, failed: bool) -> str | None:
        self._protocol_errors = self._protocol_errors + 1 if failed else 0
        return "repeated_protocol_error" if self._protocol_errors >= 2 else None

    def observe_compaction_overflow(self, overflow: bool) -> str | None:
        self._post_compaction_overflows = self._post_compaction_overflows + 1 if overflow else 0
        return (
            "context_still_over_budget"
            if self._post_compaction_overflows >= self.max_compactions_per_run
            else None
        )


def tool_call_fingerprint(
    calls: tuple[ModelToolCall, ...],
    workspace_version: int,
    reconciliation_epoch: int,
) -> str:
    return _hash([
        {
            "toolName": call.name,
            "canonicalArguments": call.arguments,
            "workspaceVersion": workspace_version,
            "reconciliationEpoch": reconciliation_epoch,
        }
        for call in calls
    ])


def _hash(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
