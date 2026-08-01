from __future__ import annotations

import hashlib
import json
import time

from eidos_runtime.db.database import Repository
from eidos_runtime.domain.long_task import (
    LongTaskProgress,
    LongTaskStatus,
    ResumeVerification,
    ResumeOutcome,
    SafePoint,
)
from eidos_runtime.persistence.errors import (
    ConditionalUpdateFailed,
    PersistenceCorruptionError,
)


CONTROL_SCOPE = "long_task/control"
_PAUSABLE_SAFE_POINTS = frozenset(
    {
        SafePoint.BEFORE_MODEL,
        SafePoint.AFTER_MODEL,
        SafePoint.WAITING_APPROVAL,
        SafePoint.WAITING_SLOT,
        SafePoint.BEFORE_TOOL,
        SafePoint.AFTER_TOOL,
        SafePoint.AFTER_CHECKPOINT,
        SafePoint.AFTER_REPOSITORY_GENERATION,
    }
)
_TERMINAL = frozenset(
    {
        LongTaskStatus.CANCELED,
        LongTaskStatus.COMPLETED,
        LongTaskStatus.FAILED,
        LongTaskStatus.INTERRUPTED,
    }
)


class LongTaskRepository(Repository):
    def initialize(self, *, run_id: str, **values: object) -> LongTaskProgress:
        if self.read(run_id) is not None:
            raise ConditionalUpdateFailed("long task already initialized")
        progress = LongTaskProgress(
            run_id=run_id,
            status=LongTaskStatus.RUNNING,
            safe_point=SafePoint.BEFORE_MODEL,
            progress_sequence=0,
            context_plan_id=_optional_text(values.get("context_plan_id")),
            context_snapshot_id=_optional_text(values.get("context_snapshot_id")),
            rule_snapshot_id=_optional_text(values.get("rule_snapshot_id")),
            inventory_snapshot_id=_optional_text(values.get("inventory_snapshot_id")),
            index_snapshot_id=_optional_text(values.get("index_snapshot_id")),
            permission_snapshot_hash=_optional_text(
                values.get("permission_snapshot_hash")
            ),
            workspace_path=_required_text(values.get("workspace_path")),
            workspace_device=_required_int(values.get("workspace_device")),
            workspace_inode=_required_int(values.get("workspace_inode")),
            workspace_owner=_required_int(values.get("workspace_owner")),
            git_head=_optional_text(values.get("git_head")),
            side_effects_may_exist=False,
            reconciliation_required=False,
            updated_at=_now_ms(),
        )
        self._insert(progress)
        return progress

    def read(self, run_id: str) -> LongTaskProgress | None:
        with self.lock:
            row = (
                self._connection()
                .execute(
                    "SELECT result_json FROM operations WHERE id = ? AND scope = ?",
                    (run_id, CONTROL_SCOPE),
                )
                .fetchone()
            )
        if row is None:
            return None
        try:
            return LongTaskProgress.model_validate_json(row["result_json"])
        except (TypeError, ValueError):
            raise PersistenceCorruptionError(
                "persistence_record_invalid", record="long_task_progress"
            ) from None

    def list_resumable(self) -> tuple[LongTaskProgress, ...]:
        with self.lock:
            rows = (
                self._connection()
                .execute(
                    "SELECT result_json FROM operations WHERE scope = ? ORDER BY created_at, id",
                    (CONTROL_SCOPE,),
                )
                .fetchall()
            )
        values: list[LongTaskProgress] = []
        for row in rows:
            try:
                progress = LongTaskProgress.model_validate_json(row["result_json"])
            except (TypeError, ValueError):
                raise PersistenceCorruptionError(
                    "persistence_record_invalid", record="long_task_progress"
                ) from None
            if progress.status not in _TERMINAL:
                values.append(progress)
        return tuple(values)

    def record_safe_point(self, run_id: str, safe_point: SafePoint) -> LongTaskProgress:
        current = self._require(run_id)
        if current.status in _TERMINAL:
            return current
        return self._update(current, safe_point=safe_point)

    def request_pause(self, run_id: str) -> LongTaskProgress:
        current = self._require(run_id)
        if current.status not in {LongTaskStatus.RUNNING}:
            raise ConditionalUpdateFailed("pause is not available")
        return self._update(
            current,
            status=LongTaskStatus.PAUSE_REQUESTED,
            pause_requested_at=_now_ms(),
        )

    def mark_paused(self, run_id: str, safe_point: SafePoint) -> LongTaskProgress:
        current = self._require(run_id)
        if (
            current.status is not LongTaskStatus.PAUSE_REQUESTED
            or safe_point not in _PAUSABLE_SAFE_POINTS
        ):
            raise ConditionalUpdateFailed("pause safe point is unavailable")
        return self._update(
            current,
            status=LongTaskStatus.PAUSED,
            safe_point=safe_point,
            paused_at=_now_ms(),
        )

    def request_resume(self, run_id: str) -> LongTaskProgress:
        current = self._require(run_id)
        if current.status is not LongTaskStatus.PAUSED:
            raise ConditionalUpdateFailed("resume is not available")
        return self._update(current, status=LongTaskStatus.RESUME_REQUESTED)

    def record_verification(
        self, run_id: str, verification: ResumeVerification
    ) -> LongTaskProgress:
        current = self._require(run_id)
        if (
            current.status is not LongTaskStatus.RESUME_REQUESTED
            or verification.run_id != run_id
        ):
            raise ConditionalUpdateFailed("resume verification is unavailable")
        return self._update(current, last_verification=verification)

    def record_restart_verification(
        self, run_id: str, verification: ResumeVerification
    ) -> LongTaskProgress:
        current = self._require(run_id)
        if verification.run_id != run_id:
            raise ConditionalUpdateFailed("restart verification is unavailable")
        if (
            current.last_verification is not None
            and current.last_verification.outcome is verification.outcome
            and current.last_verification.reasons == verification.reasons
        ):
            return current
        return self._update(current, last_verification=verification)

    def mark_resumed(self, run_id: str) -> LongTaskProgress:
        current = self._require(run_id)
        if (
            current.status is not LongTaskStatus.RESUME_REQUESTED
            or current.last_verification is None
            or current.last_verification.outcome is not ResumeOutcome.SAFE_RESUME
        ):
            raise ConditionalUpdateFailed("resume requires verified state")
        return self._update(
            current,
            status=LongTaskStatus.RUNNING,
            safe_point=SafePoint.BEFORE_MODEL,
            resumed_at=_now_ms(),
        )

    def request_cancel(self, run_id: str) -> LongTaskProgress:
        current = self._require(run_id)
        if current.status in _TERMINAL:
            return current
        return self._update(
            current,
            status=LongTaskStatus.CANCEL_REQUESTED,
            cancel_requested_at=_now_ms(),
        )

    def mark_canceled(
        self, run_id: str, *, side_effects_may_exist: bool = False
    ) -> LongTaskProgress:
        current = self._require(run_id)
        if current.status is not LongTaskStatus.CANCEL_REQUESTED:
            raise ConditionalUpdateFailed("cancel settlement is unavailable")
        return self._update(
            current,
            status=LongTaskStatus.CANCELED,
            side_effects_may_exist=(
                current.side_effects_may_exist or side_effects_may_exist
            ),
            reconciliation_required=(
                current.reconciliation_required or side_effects_may_exist
            ),
        )

    def mark_completed(self, run_id: str) -> LongTaskProgress:
        current = self._require(run_id)
        if current.status is not LongTaskStatus.RUNNING:
            raise ConditionalUpdateFailed("completion is unavailable")
        return self._update(current, status=LongTaskStatus.COMPLETED)

    def mark_interrupted(self, run_id: str) -> LongTaskProgress:
        current = self._require(run_id)
        if current.status in _TERMINAL:
            return current
        return self._update(current, status=LongTaskStatus.INTERRUPTED)

    def _insert(self, progress: LongTaskProgress) -> None:
        request_hash = _hash({"runId": progress.run_id})
        with self.lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO operations (id, scope, request_hash, status, result_json, created_at)
                VALUES (?, ?, ?, 'in_progress', ?, ?)
                """,
                (
                    progress.run_id,
                    CONTROL_SCOPE,
                    request_hash,
                    progress.model_dump_json(),
                    progress.updated_at,
                ),
            )

    def _update(self, current: LongTaskProgress, **changes: object) -> LongTaskProgress:
        updated = current.model_copy(
            update={
                **changes,
                "progress_sequence": current.progress_sequence + 1,
                "updated_at": _now_ms(),
            }
        )
        with self.lock, self._connection() as connection:
            changed = connection.execute(
                """
                UPDATE operations
                SET result_json = ?
                WHERE id = ? AND scope = ? AND status = 'in_progress'
                  AND result_json = ?
                """,
                (
                    updated.model_dump_json(),
                    current.run_id,
                    CONTROL_SCOPE,
                    current.model_dump_json(),
                ),
            )
            if changed.rowcount != 1:
                raise ConditionalUpdateFailed("long task compare-and-set failed")
        return updated

    def _require(self, run_id: str) -> LongTaskProgress:
        value = self.read(run_id)
        if value is None:
            raise ConditionalUpdateFailed("long task is unavailable")
        return value


class ResumeVerifier:
    @staticmethod
    def verify(
        *,
        run_id: str,
        expected_workspace: tuple[str, int, int, int],
        current_workspace: tuple[str, int, int, int],
        expected_git_head: str | None,
        current_git_head: str | None,
        expected_rule_snapshot_id: str | None,
        current_rule_snapshot_id: str | None,
        expected_index_snapshot_id: str | None,
        current_index_snapshot_id: str | None,
        side_effects_may_exist: bool,
        expected_context_plan_id: str | None = None,
        current_context_plan_id: str | None = None,
        expected_permission_snapshot_hash: str | None = None,
        current_permission_snapshot_hash: str | None = None,
        pending_approval: bool = False,
        unfinished_side_effecting_tool: bool = False,
        durable_intent_unfinished: bool = False,
        model_available: bool = True,
        credential_available: bool = True,
        seatbelt_ready: bool = True,
        mcp_configuration_matches: bool = True,
        checkpoint_integrity_valid: bool = True,
    ) -> ResumeVerification:
        reasons: list[str] = []
        if expected_workspace != current_workspace:
            reasons.append("workspace_identity_changed")
        if expected_git_head != current_git_head:
            reasons.append("git_head_changed")
        if expected_rule_snapshot_id != current_rule_snapshot_id:
            reasons.append("rule_snapshot_changed")
        if expected_index_snapshot_id != current_index_snapshot_id:
            reasons.append("index_snapshot_changed")
        if expected_context_plan_id != current_context_plan_id:
            reasons.append("context_plan_changed")
        if expected_permission_snapshot_hash != current_permission_snapshot_hash:
            reasons.append("permission_snapshot_changed")
        if side_effects_may_exist:
            reasons.append("reconciliation_required")
        if pending_approval:
            reasons.append("approval_required")
        if unfinished_side_effecting_tool or durable_intent_unfinished:
            reasons.append("reconciliation_required")
        if not model_available or not credential_available:
            reasons.append("model_unavailable")
        if not seatbelt_ready:
            reasons.append("permission_changed")
        if not mcp_configuration_matches:
            reasons.append("mcp_configuration_changed")
        if not checkpoint_integrity_valid:
            reasons.append("checkpoint_integrity_failed")
        reasons = list(dict.fromkeys(reasons))
        outcome = ResumeOutcome.SAFE_RESUME
        if "reconciliation_required" in reasons:
            outcome = ResumeOutcome.RECONCILIATION_REQUIRED
        elif "permission_snapshot_changed" in reasons:
            outcome = ResumeOutcome.PERMISSION_CHANGED
        elif "permission_changed" in reasons:
            outcome = ResumeOutcome.PERMISSION_CHANGED
        elif "approval_required" in reasons:
            outcome = ResumeOutcome.APPROVAL_REQUIRED
        elif "model_unavailable" in reasons:
            outcome = ResumeOutcome.MODEL_UNAVAILABLE
        elif "checkpoint_integrity_failed" in reasons:
            outcome = ResumeOutcome.CANNOT_RESUME
        elif "workspace_identity_changed" in reasons or "git_head_changed" in reasons:
            outcome = ResumeOutcome.WORKSPACE_CHANGED
        elif "index_snapshot_changed" in reasons:
            outcome = ResumeOutcome.REINDEX_REQUIRED
        elif "rule_snapshot_changed" in reasons or "context_plan_changed" in reasons:
            outcome = ResumeOutcome.REBUILD_CONTEXT
        elif "mcp_configuration_changed" in reasons:
            outcome = ResumeOutcome.REBUILD_CONTEXT
        return ResumeVerification(
            run_id=run_id,
            outcome=outcome,
            reasons=tuple(reasons),
            checked_at=_now_ms(),
        )


class RestartVerifier:
    @staticmethod
    def verify(
        progress: LongTaskProgress, verification: ResumeVerification
    ) -> ResumeVerification:
        if (
            progress.side_effects_may_exist
            and "reconciliation_required" not in verification.reasons
        ):
            return verification.model_copy(
                update={
                    "outcome": ResumeOutcome.RECONCILIATION_REQUIRED,
                    "reasons": (*verification.reasons, "reconciliation_required"),
                }
            )
        return verification


def _required_text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("long task text value is required")
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return _required_text(value)


def _required_int(value: object) -> int:
    if type(value) is not int:
        raise ValueError("long task integer value is required")
    return value


def _now_ms() -> int:
    return int(time.time() * 1000)


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "LongTaskRepository",
    "RestartVerifier",
    "ResumeVerifier",
]
