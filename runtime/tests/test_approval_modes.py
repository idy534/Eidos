from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import threading

import pytest
from pydantic import ValidationError

from eidos_runtime.db.errors import InvalidRunStateError
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.approval_policy import ApprovalReview
from eidos_runtime.model.client import (
    ModelContextItem,
    ModelProfileSnapshot,
    ModelResponse,
    ModelToolDefinitionLike,
    ModelToolCall,
)
from eidos_runtime.model.config import default_profile_snapshot
from eidos_runtime.protocol.methods import RunStartRequestDto
from eidos_runtime.runtime.approval import ApprovalCoordinator
from eidos_runtime.runtime.approval_review import (
    MAX_REVIEW_INPUT_BYTES,
    review_approval,
)
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.runtime.state_machine import RuntimePhaseTracker
from eidos_runtime.sandbox.permissions import BasePermissionProfile


@dataclass
class _Reviewer:
    profile_snapshot: ModelProfileSnapshot
    response: ModelResponse
    calls: list[tuple[tuple[ModelContextItem, ...], bool, tuple[ModelToolDefinitionLike, ...]]] = field(
        default_factory=list
    )

    def complete(
        self,
        context: tuple[ModelContextItem, ...],
        _cancel: threading.Event,
        on_text_delta,
        *,
        instructions: str,
        allow_tools: bool = True,
        tool_definitions: tuple[ModelToolDefinitionLike, ...] = (),
    ) -> ModelResponse:
        self.calls.append((context, allow_tools, tool_definitions))
        if self.response.text:
            on_text_delta(self.response.text)
        return self.response


def _reviewer(
    *,
    outcome: str = "allow",
    risk: str = "low",
    rationale: str = "The action is limited to the requested workspace.",
    supports_tools: bool = True,
) -> _Reviewer:
    profile = default_profile_snapshot("deepseek-v4-flash").model_copy(
        update={"supports_tools": supports_tools}
    )
    if supports_tools:
        response = ModelResponse(tool_calls=(
            ModelToolCall(
                "review-call",
                "submit_approval_assessment",
                {"outcome": outcome, "risk": risk, "rationale": rationale},
            ),
        ))
    else:
        response = ModelResponse(text=json.dumps({
            "outcome": outcome,
            "risk": risk,
            "rationale": rationale,
        }))
    return _Reviewer(profile, response)


def _store(tmp_path: Path) -> tuple[SessionStore, dict[str, object]]:
    data = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data.mkdir(mode=0o700)
    workspace.mkdir()
    store = SessionStore(data)
    store.initialize()
    return store, store.create_session(str(workspace))


def _approval_fixture(
    tmp_path: Path,
    mode: str,
) -> tuple[SessionStore, dict[str, object], dict[str, object]]:
    store, session = _store(tmp_path)
    run, _ = store.create_run(
        str(session["id"]),
        "write the requested file",
        approval_mode=mode,
    )
    step = store.increment_model_step(str(run["id"]))
    item = store.create_tool_item(
        str(run["id"]), step, 0, f"call-{mode}", "write_file", "{}"
    )
    return store, run, item


def test_run_start_requires_the_full_access_confirmation_and_rejects_mismatch() -> None:
    values = {
        "sessionId": "00000000-0000-0000-0000-000000000001",
        "userInput": "inspect the workspace",
        "modelId": "deepseek-v4-flash",
    }

    with pytest.raises(ValidationError):
        RunStartRequestDto.model_validate({**values, "approvalMode": "full_access"})
    with pytest.raises(ValidationError):
        RunStartRequestDto.model_validate({
            **values,
            "approvalMode": "manual",
            "fullAccessConfirmation": "full-access-v1",
        })

    request = RunStartRequestDto.model_validate({
        **values,
        "approvalMode": "full_access",
        "fullAccessConfirmation": "full-access-v1",
    })
    assert request.approval_mode == "full_access"


def test_run_resolution_snapshot_freezes_mode_and_full_access_permissions(tmp_path: Path) -> None:
    store, session = _store(tmp_path)
    try:
        manual, _ = store.create_run(str(session["id"]), "manual")
        full_workspace = tmp_path / "full-workspace"
        full_workspace.mkdir()
        full_session = store.create_session(str(full_workspace))
        full, _ = store.create_run(
            str(full_session["id"]),
            "full access",
            approval_mode="full_access",
        )

        assert store.read_run(str(manual["id"])).get("approvalMode", "manual") == "manual"
        assert full["approvalMode"] == "full_access"
        snapshot = store.read_run_resolution_snapshot(str(full["id"]))
        policy = json.loads(snapshot.sandbox_policy_json)
        permissions = BasePermissionProfile.model_validate_json(
            snapshot.permission_profile_json
        )
        assert policy["approvalMode"] == "full_access"
        assert policy["fullAccessConfirmation"] == "full-access-v1"
        assert policy["sandboxType"] == "none"
        assert permissions.full_access is True
        assert permissions.network_enabled is True
        assert permissions.permanent_denies == ()
        assert permissions.protected_write_paths == ()
    finally:
        store.close()


def test_review_approval_requires_valid_structured_output_and_denies_unknown_risk() -> None:
    reviewer = _reviewer()

    approved = review_approval(reviewer, '{"tool":"write_file"}', threading.Event())
    assert approved.decision == "approve"
    assert approved.reason_code == "auto_review_approved"
    assert reviewer.calls[0][1] is True
    assert reviewer.calls[0][2][0].name == "submit_approval_assessment"

    unknown = _reviewer(risk="unknown")
    rejected = review_approval(unknown, '{"tool":"write_file"}', threading.Event())
    assert rejected.decision == "reject"
    assert rejected.reason_code == "auto_review_rejected"


def test_review_approval_supports_strict_json_fallback_and_bounded_evidence() -> None:
    reviewer = _reviewer(supports_tools=False)
    result = review_approval(reviewer, "read the file", threading.Event())
    assert result.decision == "approve"
    assert reviewer.calls[0][1] is False
    assert reviewer.calls[0][2] == ()

    oversized = _reviewer()
    result = review_approval(
        oversized,
        "x" * (MAX_REVIEW_INPUT_BYTES + 1),
        threading.Event(),
    )
    assert result.decision == "reject"
    assert result.reason_code == "auto_review_input_too_large"
    assert oversized.calls == []


def test_auto_review_persists_decision_and_deduplicates_the_same_rejected_action(
    tmp_path: Path,
) -> None:
    store, run, item = _approval_fixture(tmp_path, "auto_review")
    reviewer = _reviewer(outcome="deny", risk="high", rationale="The target is too broad.")
    transport_calls = 0

    def transport(_request, _cancel):
        nonlocal transport_calls
        transport_calls += 1
        raise AssertionError("auto review must not call the user transport")

    coordinator = ApprovalCoordinator(
        store,
        transport,
        RuntimeEvents(lambda _message: None),
        RuntimePhaseTracker(),
        lambda _run_id: None,
        lambda: None,
        lambda _run_id, _cancel: None,
        lambda _run_id, _cancel: None,
        requeue=False,
        reviewer=reviewer,
    )
    try:
        first = coordinator.request(
            str(run["id"]),
            item,
            {"kind": "file_change"},
            threading.Event(),
            transition_reason="file_change_approval",
        )
        assert first.decision == "reject"
        assert coordinator.last_review is not None
        assert coordinator.last_review.reason_code == "auto_review_rejected"

        approval_id = store.connection.execute(
            "SELECT id FROM approvals WHERE item_id = ?", (item["id"],)
        ).fetchone()[0]
        persisted = store.typed_runtime_repository().read_approval(approval_id)
        assert persisted is not None
        assert persisted.review == coordinator.last_review
        assert transport_calls == 0

        second_item = store.create_tool_item(
            str(run["id"]), 2, 0, "call-same-action", "write_file", "{}"
        )
        second = coordinator.request(
            str(run["id"]),
            second_item,
            {"kind": "file_change"},
            threading.Event(),
            transition_reason="file_change_approval",
        )
        assert second.decision == "reject"
        assert len(reviewer.calls) == 1
    finally:
        store.close()


def test_approval_transition_rejects_a_review_source_that_does_not_match_run_mode(
    tmp_path: Path,
) -> None:
    store, run, item = _approval_fixture(tmp_path, "manual")
    try:
        store.begin_approval(item["id"], "", None)
        review = ApprovalReview(
            source="model",
            decision="approve",
            reason_code="auto_review_approved",
            rationale="approved",
            risk="low",
        )
        with pytest.raises(InvalidRunStateError, match="approval source"):
            store.resolve_approval_committed(
                item["id"], "approve", None, review=review
            )
    finally:
        store.close()


def test_full_access_approval_is_recorded_as_mode_authorization(tmp_path: Path) -> None:
    store, run, item = _approval_fixture(tmp_path, "full_access")
    coordinator = ApprovalCoordinator(
        store,
        lambda _request, _cancel: (_ for _ in ()).throw(
            AssertionError("full access must not ask the user")
        ),
        RuntimeEvents(lambda _message: None),
        RuntimePhaseTracker(),
        lambda _run_id: None,
        lambda: None,
        lambda _run_id, _cancel: None,
        lambda _run_id, _cancel: None,
        requeue=False,
    )
    try:
        outcome = coordinator.request(
            str(run["id"]),
            item,
            {"kind": "file_change"},
            threading.Event(),
            transition_reason="file_change_approval",
        )
        assert outcome.decision == "approve"
        assert coordinator.last_review is not None
        assert coordinator.last_review.source == "mode"
        assert coordinator.last_review.reason_code == "full_access"
    finally:
        store.close()
