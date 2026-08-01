from __future__ import annotations

from pathlib import Path

import pytest

from eidos_runtime.db.database import Database
from eidos_runtime.persistence.errors import ConditionalUpdateFailed
from eidos_runtime.runtime.long_task import (
    LongTaskRepository,
    LongTaskStatus,
    ResumeVerification,
    SafePoint,
    VerificationStatus,
)


def test_long_task_pause_resume_cancel_uses_conditional_sqlite_facts(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    database = Database(data)
    database.initialize()
    try:
        repository = LongTaskRepository(database)
        created = repository.initialize(
            run_id="run-1",
            workspace_path="/workspace",
            workspace_device=1,
            workspace_inode=2,
            workspace_owner=3,
            git_head="head-1",
            rule_snapshot_id="rule-1",
            inventory_snapshot_id="inventory-1",
            index_snapshot_id="index-1",
            context_plan_id="plan-1",
        )
        assert created.status is LongTaskStatus.RUNNING
        requested = repository.request_pause("run-1")
        assert requested.status is LongTaskStatus.PAUSE_REQUESTED
        paused = repository.mark_paused("run-1", SafePoint.BEFORE_MODEL)
        assert paused.status is LongTaskStatus.PAUSED
        resumed = repository.request_resume("run-1")
        assert resumed.status is LongTaskStatus.RESUME_REQUESTED
        verified = repository.record_verification(
            "run-1",
            ResumeVerification(
                run_id="run-1",
                status=VerificationStatus.VERIFIED,
                reasons=(),
                checked_at=10,
            ),
        )
        assert verified.last_verification is not None
        running = repository.mark_resumed("run-1")
        assert running.status is LongTaskStatus.RUNNING
        cancel_requested = repository.request_cancel("run-1")
        assert cancel_requested.status is LongTaskStatus.CANCEL_REQUESTED
        canceled = repository.mark_canceled("run-1", side_effects_may_exist=True)
        assert canceled.status is LongTaskStatus.CANCELED
        assert canceled.side_effects_may_exist is True
        with pytest.raises(ConditionalUpdateFailed):
            repository.mark_completed("run-1")
    finally:
        database.close()


def test_resume_verification_blocks_changed_workspace_or_uncertain_side_effects() -> None:
    from eidos_runtime.runtime.long_task import ResumeVerifier

    verification = ResumeVerifier.verify(
        run_id="run-1",
        expected_workspace=("/workspace", 1, 2, 3),
        current_workspace=("/workspace", 1, 2, 9),
        expected_git_head="head-1",
        current_git_head="head-2",
        expected_rule_snapshot_id="rule-1",
        current_rule_snapshot_id="rule-1",
        expected_index_snapshot_id="index-1",
        current_index_snapshot_id="index-1",
        side_effects_may_exist=True,
    )

    assert verification.status is VerificationStatus.BLOCKED
    assert "workspace_identity_changed" in verification.reasons
    assert "git_head_changed" in verification.reasons
    assert "reconciliation_required" in verification.reasons
