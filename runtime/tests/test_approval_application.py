from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from eidos_runtime.application.approvals import (
    ApprovalApplication,
    ApprovalDecision,
)
from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.db.database import Database
from eidos_runtime.domain.tool import Approval, ApprovalStatus
from eidos_runtime.persistence.repositories import TypedRuntimeRepository


@dataclass
class RecordingApprovalRuntime:
    accepted: bool = True
    responses: list[tuple[str, ApprovalDecision, str | None]] = field(
        default_factory=list
    )

    def submit_approval_response(
        self,
        *,
        request_id: str,
        decision: ApprovalDecision,
        feedback: str | None,
    ) -> bool:
        self.responses.append((request_id, decision, feedback))
        return self.accepted


def _pending_approval_repository(
    tmp_path: Path,
) -> tuple[Database, TypedRuntimeRepository]:
    database = Database(tmp_path / "data")
    database.initialize()
    connection = database.connection()
    connection.execute(
        """
        INSERT INTO sessions (id, workspace_root, created_at, updated_at)
        VALUES ('session-1', ?, 1000, 1000)
        """,
        (str(tmp_path / "workspace"),),
    )
    connection.execute(
        """
        INSERT INTO runs (
            id, session_id, user_input, model_id, model_profile_json, status,
            created_at, updated_at
        ) VALUES ('run-1', 'session-1', 'goal', 'model-1', '{}', 'waiting_approval', 1000, 1000)
        """
    )
    connection.execute(
        """
        INSERT INTO items (
            id, session_id, run_id, ordinal, kind, status, created_at
        ) VALUES ('item-1', 'session-1', 'run-1', 0, 'tool_call', 'in_progress', 1000)
        """
    )
    connection.execute(
        """
        INSERT INTO tool_calls (
            id, item_id, model_step_index, batch_order, provider_call_id,
            tool_name, status, arguments_json, approval_status, started_at
        ) VALUES (
            'tool-call-1', 'item-1', 0, 0, 'provider-call-1',
            'write_file', 'running', '{}', 'pending', 1000
        )
        """
    )
    connection.execute(
        """
        INSERT INTO approvals (
            id, tool_call_id, run_id, item_id, status, request_hash,
            request_json, attempt_ordinal, approval_kind, created_at
        ) VALUES (
            'approval-1', 'tool-call-1', 'run-1', 'item-1', 'pending', ?,
            '{}', 0, 'tool', 1000
        )
        """,
        ("a" * 64,),
    )
    connection.commit()
    return database, TypedRuntimeRepository(database)


def test_approval_application_reads_typed_pending_approval(tmp_path: Path) -> None:
    database, repository = _pending_approval_repository(tmp_path)
    try:
        application = ApprovalApplication(repository, RecordingApprovalRuntime())

        pending = application.pending("run-1")
        read = application.read("approval-1")

        assert isinstance(pending, Approval)
        assert pending.status is ApprovalStatus.PENDING
        assert read == pending
        assert application.pending("missing-run") is None
    finally:
        database.close()


def test_approval_application_maps_missing_approval_to_application_error(
    tmp_path: Path,
) -> None:
    database, repository = _pending_approval_repository(tmp_path)
    try:
        application = ApprovalApplication(repository, RecordingApprovalRuntime())

        with pytest.raises(ApplicationError, match="approval not found") as error:
            application.read("missing")

        assert error.value.code == "RESOURCE_NOT_FOUND"
    finally:
        database.close()


def test_approval_application_submits_approve_reject_and_feedback_through_runtime_port(
    tmp_path: Path,
) -> None:
    database, repository = _pending_approval_repository(tmp_path)
    try:
        runtime = RecordingApprovalRuntime()
        application = ApprovalApplication(repository, runtime)

        approved = application.approve("request-approve")
        rejected = application.reject("request-reject", feedback="do not write")
        feedback = application.submit_feedback("request-feedback", "show a diff")

        assert approved.accepted is True
        assert rejected.accepted is True
        assert feedback.accepted is True
        assert runtime.responses == [
            ("request-approve", ApprovalDecision.APPROVE, None),
            ("request-reject", ApprovalDecision.REJECT, "do not write"),
            ("request-feedback", ApprovalDecision.REJECT, "show a diff"),
        ]
    finally:
        database.close()


def test_approval_application_reports_when_runtime_no_longer_has_the_pending_request(
    tmp_path: Path,
) -> None:
    database, repository = _pending_approval_repository(tmp_path)
    try:
        application = ApprovalApplication(
            repository, RecordingApprovalRuntime(accepted=False)
        )

        result = application.approve("late-request")

        assert result.accepted is False
        assert result.reason == "approval_not_pending"
    finally:
        database.close()
