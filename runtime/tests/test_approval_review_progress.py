from __future__ import annotations

import json
from pathlib import Path
import threading

from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.model.config import default_profile_snapshot
from eidos_runtime.protocol.schemas import ApprovalReviewSummaryDto, ToolCallDto
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.runtime.approval import ApprovalCoordinator
from eidos_runtime.runtime.approval_review import review_approval
from eidos_runtime.runtime.engine import RuntimeEngine


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "protocol" / "fixtures" / "run-approval-review-progress-v1.json"


def _tool_item_from_snapshot(snapshot: dict[str, object], tool_name: str = "apply_patch") -> dict[str, object]:
    items = snapshot["items"]
    assert isinstance(items, list)
    for item in items:
        assert isinstance(item, dict)
        tool_call = item.get("toolCall")
        if isinstance(tool_call, dict) and tool_call.get("toolName") == tool_name:
            return item
    raise AssertionError(f"tool item {tool_name!r} not found")


def test_auto_review_emits_transient_progress_and_projects_summary(tmp_path: Path, monkeypatch) -> None:
    data, workspace = tmp_path / "data", tmp_path / "workspace"
    data.mkdir(mode=0o700)
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("before\n", encoding="utf-8")

    store = SessionStore(data)
    store.initialize()
    monkeypatch.setattr("eidos_runtime.runtime.tool_runtime.is_seatbelt_ready", lambda: False)

    session = store.create_session(str(workspace))
    run, _ = store.create_run(session["id"], "Update outside.txt", approval_mode="auto_review")

    patch_call = ModelToolCall(
        "call-1",
        "apply_patch",
        {"patch": f"*** Begin Patch\n*** Update File: {outside}\n@@\n-before\n+after\n*** End Patch"},
    )
    triage = ModelToolCall(
        "triage-1",
        "submit_approval_assessment",
        {
            "outcome": "allow",
            "risk": "low",
            "rationale": "The user explicitly asked to update outside.txt.",
        },
    )
    model = ScriptedModel([
        ModelResponse(tool_calls=(patch_call,)),
        ModelResponse(tool_calls=(triage,)),
        ModelResponse(text="done"),
    ])
    model.profile_snapshot = default_profile_snapshot("deepseek-v4-flash")

    notifications: list[dict[str, object]] = []
    try:
        RuntimeEngine(store, model, notifications.append).run(run["id"], threading.Event())
        progress = [n for n in notifications if n.get("method") == "run/approvalReviewProgress"]
        stages = [n["params"]["stage"] for n in progress]  # type: ignore[index]
        assert stages[0] == "preparing"
        assert "evaluating" in stages
        assert "deciding" in stages
        assert stages[-1] == "finished"
        assert all(n["params"]["totalSteps"] == 3 for n in progress)  # type: ignore[index]
        assert all(n["params"]["runId"] == run["id"] for n in progress)  # type: ignore[index]
        assert all(n["params"]["sessionId"] == session["id"] for n in progress)  # type: ignore[index]

        persisted_events = [
            row["event_type"]
            for row in store.connection.execute("SELECT event_type FROM events").fetchall()
        ]
        assert "run/approvalReviewProgress" not in persisted_events

        snapshot = store.read_session_snapshot(session["id"])
        item = _tool_item_from_snapshot(snapshot)
        tool_call = item["toolCall"]
        assert isinstance(tool_call, dict)
        review = tool_call.get("approvalReview")
        assert isinstance(review, dict)
        assert review["source"] == "model"
        assert review["decision"] == "approve"
        assert review["reasonCode"] == "auto_review_approved"
        assert review["risk"] == "low"
        assert "outside.txt" in review["rationale"]

        read_back = store.read_item(str(item["id"]))
        assert read_back is not None
        assert read_back["toolCall"]["approvalReview"] == review

        validated = ToolCallDto.model_validate(tool_call)
        assert isinstance(validated.approval_review, ApprovalReviewSummaryDto)
        assert validated.approval_review.risk == "low"
        assert outside.read_text(encoding="utf-8") == "after\n"
    finally:
        store.close()


def test_auto_review_rejection_projects_approval_review_summary(tmp_path: Path, monkeypatch) -> None:
    data, workspace = tmp_path / "data", tmp_path / "workspace"
    data.mkdir(mode=0o700)
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("before\n", encoding="utf-8")

    store = SessionStore(data)
    store.initialize()
    monkeypatch.setattr("eidos_runtime.runtime.tool_runtime.is_seatbelt_ready", lambda: False)

    session = store.create_session(str(workspace))
    run, _ = store.create_run(session["id"], "Read workspace only", approval_mode="auto_review")

    patch_call = ModelToolCall(
        "call-1",
        "apply_patch",
        {"patch": f"*** Begin Patch\n*** Update File: {outside}\n@@\n-before\n+after\n*** End Patch"},
    )
    triage = ModelToolCall(
        "triage-1",
        "submit_approval_assessment",
        {
            "outcome": "deny",
            "risk": "high",
            "rationale": "The user did not ask to modify files outside the workspace.",
        },
    )
    model = ScriptedModel([
        ModelResponse(tool_calls=(patch_call,)),
        ModelResponse(tool_calls=(triage,)),
        ModelResponse(text="stopped"),
    ])
    model.profile_snapshot = default_profile_snapshot("deepseek-v4-flash")

    try:
        RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
        snapshot = store.read_session_snapshot(session["id"])
        item = _tool_item_from_snapshot(snapshot)
        review = item["toolCall"]["approvalReview"]
        assert review["source"] == "model"
        assert review["decision"] == "reject"
        assert review["reasonCode"] == "auto_review_rejected"
        assert review["risk"] == "high"
        assert "modify files outside the workspace" in review["rationale"]
        assert outside.read_text(encoding="utf-8") == "before\n"
    finally:
        store.close()


def test_auto_review_stage_callback_reports_retry_and_redacts_secrets(tmp_path: Path, monkeypatch) -> None:
    data, workspace = tmp_path / "data", tmp_path / "workspace"
    data.mkdir(mode=0o700)
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("before\n", encoding="utf-8")

    store = SessionStore(data)
    store.initialize()
    monkeypatch.setattr("eidos_runtime.runtime.tool_runtime.is_seatbelt_ready", lambda: False)

    session = store.create_session(str(workspace))
    run, _ = store.create_run(session["id"], "Deploy key check", approval_mode="auto_review")

    patch_call = ModelToolCall(
        "call-1",
        "apply_patch",
        {
            "patch": f"*** Begin Patch\n*** Update File: {outside}\n@@\n-before\n+after\n*** End Patch",
        },
    )
    model = ScriptedModel([
        ModelResponse(tool_calls=(patch_call,)),
        ModelResponse(text="not-a-tool-call"),
        ModelResponse(tool_calls=(
            ModelToolCall(
                "triage-2",
                "submit_approval_assessment",
                {
                    "outcome": "allow",
                    "risk": "unknown",
                    "rationale": "Confirmed intent.",
                },
            ),
        )),
        ModelResponse(text="done"),
    ])
    model.profile_snapshot = default_profile_snapshot("deepseek-v4-flash")

    notifications: list[dict[str, object]] = []
    try:
        RuntimeEngine(store, model, notifications.append).run(run["id"], threading.Event())
        progress = [n for n in notifications if n.get("method") == "run/approvalReviewProgress"]
        stages = [n["params"]["stage"] for n in progress]  # type: ignore[index]
        assert "retrying" in stages
        assert stages[-1] == "finished"

        snapshot = store.read_session_snapshot(session["id"])
        item = _tool_item_from_snapshot(snapshot)
        dto = ToolCallDto.model_validate(item["toolCall"])
        dumped = dto.to_json_value()
        assert isinstance(dumped, dict)
        # risk must stay present even when "unknown"
        assert dumped["approvalReview"]["risk"] == "unknown"  # type: ignore[index]
    finally:
        store.close()


def test_review_approval_on_stage_invoked_and_fixture_is_valid() -> None:
    stages: list[tuple[str, int | None]] = []
    model = ScriptedModel([
        ModelResponse(tool_calls=(
            ModelToolCall(
                "triage-1",
                "submit_approval_assessment",
                {
                    "outcome": "allow",
                    "risk": "low",
                    "rationale": "Safe read/write within stated task.",
                },
            ),
        ))
    ])
    model.profile_snapshot = default_profile_snapshot("deepseek-v4-flash")

    result = review_approval(
        model,
        json.dumps({
            "goal": "Update outside.txt",
            "approval": {"kind": "file_change", "summary": "Write outside.txt"},
        }),
        threading.Event(),
        on_stage=lambda stage, attempt: stages.append((stage, attempt)),
    )
    assert result.decision == "approve"
    assert ("evaluating", 1) in stages
    assert ("deciding", 1) in stages

    secret = "secret_token_1234567890abcdef"
    redacted_title, redacted_preview = ApprovalCoordinator._approval_progress_meta(
        {
            "kind": "command_execution",
            "displayIntent": "检查接口",
            "command": f"curl -H 'Authorization: Bearer {secret}' https://api.example.com",
            "networkEnabled": True,
        },
        {},
    )
    assert redacted_title == "检查接口（联网）"
    assert redacted_preview is not None
    assert secret not in redacted_preview
    assert "api.example.com" in redacted_preview

    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    for key in ("preparingNotification", "evaluatingNotification", "finishedNotification"):
        msg = fixture[key]
        assert msg["jsonrpc"] == "2.0"
        assert msg["method"] == "run/approvalReviewProgress"
        assert isinstance(msg["params"]["sessionId"], str)
        assert isinstance(msg["params"]["runId"], str)
        assert isinstance(msg["params"]["itemId"], str)
