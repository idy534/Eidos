from __future__ import annotations

import hashlib
import json

from eidos_runtime.model.client import ModelToolCall


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
        self._last_error: str | None = None
        self._same_error = 0
        self._last_progress: tuple[int, str | None] | None = None
        self._no_progress = 0
        self._empty_responses = 0
        self._protocol_errors = 0
        self._sensitive_calls = 0
        self._approval_rejections = 0
        self._post_compaction_overflows = 0

    def observe_tool_calls(
        self,
        calls: tuple[ModelToolCall, ...],
        workspace_version: int,
        reconciliation_epoch: int,
    ) -> str | None:
        fingerprint = _hash([
            {
                "toolName": call.name,
                "canonicalArguments": call.arguments,
                "workspaceVersion": workspace_version,
                "reconciliationEpoch": reconciliation_epoch,
            }
            for call in calls
        ])
        self._same_tool = self._same_tool + 1 if fingerprint == self._last_tool else 1
        self._last_tool = fingerprint
        return (
            "repeated_tool_call"
            if self._same_tool >= self.max_same_tool_fingerprint
            else None
        )

    def observe_errors(self, errors: tuple[str, ...]) -> str | None:
        for fingerprint in errors:
            self._same_error = self._same_error + 1 if fingerprint == self._last_error else 1
            self._last_error = fingerprint
            if self._same_error >= self.max_same_error_fingerprint:
                return "repeated_tool_error"
        if not errors:
            self._last_error = None
            self._same_error = 0
        return None

    def observe_progress(self, workspace_version: int, diff_hash: str | None) -> str | None:
        current = (workspace_version, diff_hash)
        unchanged = (
            self._last_progress is not None
            and (
                workspace_version == self._last_progress[0]
                or (diff_hash is not None and diff_hash == self._last_progress[1])
            )
        )
        self._no_progress = self._no_progress + 1 if unchanged else 1
        self._last_progress = current
        return "no_progress" if self._no_progress >= self.max_no_progress_rounds else None

    def observe_empty_response(self, empty: bool) -> str | None:
        self._empty_responses = self._empty_responses + 1 if empty else 0
        return "repeated_empty_response" if self._empty_responses >= 2 else None

    def observe_protocol_error(self, failed: bool) -> str | None:
        self._protocol_errors = self._protocol_errors + 1 if failed else 0
        return "repeated_protocol_error" if self._protocol_errors >= 2 else None

    def observe_sensitive_tool_call(self, rejected: bool) -> str | None:
        self._sensitive_calls = self._sensitive_calls + 1 if rejected else 0
        return "repeated_sensitive_tool_input" if self._sensitive_calls >= 2 else None

    def observe_approval_rejection(self, rejected: bool) -> str | None:
        self._approval_rejections = self._approval_rejections + 1 if rejected else 0
        return "repeated_approval_rejection" if self._approval_rejections >= 2 else None

    def observe_compaction_overflow(self, overflow: bool) -> str | None:
        self._post_compaction_overflows = self._post_compaction_overflows + 1 if overflow else 0
        return (
            "context_still_over_budget"
            if self._post_compaction_overflows >= self.max_compactions_per_run
            else None
        )


def _hash(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
