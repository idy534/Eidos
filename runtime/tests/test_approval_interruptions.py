"""Approvals interrupt the user only when the user's answer is needed."""
from __future__ import annotations

import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.approval_policy import ApprovalReview
from eidos_runtime.extensions.plugins import PluginCatalog
from eidos_runtime.extensions.skills import SkillCatalog
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.model.config import default_profile_snapshot
from eidos_runtime.runtime.approval import ApprovalCoordinator, ApprovalDecision
from eidos_runtime.runtime.approval_review import MAX_REVIEW_INPUT_BYTES, build_review_evidence
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.runtime.state_machine import RuntimePhaseTracker


class _Reviewer:
    """Returns one fixed assessment and records each review."""

    def __init__(self, outcome: str, risk: str, *, fail: BaseException | None = None) -> None:
        self.profile_snapshot = default_profile_snapshot("deepseek-v4-flash")
        self.calls = 0
        self.fail = fail
        self.response = ModelResponse(tool_calls=(ModelToolCall("review", "submit_approval_assessment", {
            "outcome": outcome, "risk": risk, "rationale": "Assessment for the test.",
        }),))

    def complete(self, *_args, **_kwargs) -> ModelResponse:
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        return self.response


def _coordinator(store: SessionStore, reviewer: object, transport) -> ApprovalCoordinator:
    return ApprovalCoordinator(
        store, transport, RuntimeEvents(lambda _: None), RuntimePhaseTracker(),
        lambda _: None, lambda: None, lambda *_: None, lambda *_: None,
        requeue=False, reviewer=reviewer,
    )


def _pending_item(tmp_path: Path, mode: str = "auto_review"):
    store = SessionStore(tmp_path / "data")
    store.initialize()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = store.create_session(str(workspace))
    run, _ = store.create_run(session["id"], "Write the report outside the project", approval_mode=mode)
    step = store.increment_model_step(str(run["id"]))
    item = store.create_tool_item(str(run["id"]), step, 0, "call", "write_file", "{}")
    return store, run, item


def test_review_interrupted_by_a_restart_runs_again_without_asking(tmp_path):
    class Crash(BaseException):
        pass

    store, run, item = _pending_item(tmp_path)
    asked = []
    try:
        with pytest.raises(Crash):
            _coordinator(store, _Reviewer("allow", "low", fail=Crash()), None).request(
                run["id"], item, {"kind": "file_change"}, threading.Event(), transition_reason="file")
        assert store.typed_runtime_repository().read_pending_approval(run["id"]) is not None
        reviewer = _Reviewer("allow", "low")
        result = _coordinator(store, reviewer, lambda request, _cancel: asked.append(request)).request(
            run["id"], item, {"kind": "file_change"}, threading.Event(), transition_reason="file")
        assert result.decision == "approve"
        assert asked == [] and reviewer.calls == 1
    finally:
        store.close()


def test_reserved_request_the_user_did_not_ask_for_is_declined_without_a_prompt(tmp_path):
    store, run, item = _pending_item(tmp_path)
    asked = []
    description = {"kind": "file_change", "requiresHumanConfirmation": True,
                   "riskWarning": "此操作会修改受保护路径。"}
    try:
        coordinator = _coordinator(store, _Reviewer("deny", "high"), lambda request, _cancel: asked.append(request))
        result = coordinator.request(run["id"], item, description, threading.Event(), transition_reason="file")
        assert result.decision == "reject" and asked == []
        row = store.connection.execute("SELECT status, review_json FROM approvals").fetchone()
        assert row["status"] == "rejected"
        review = ApprovalReview.model_validate_json(row["review_json"])
        assert (review.source, review.reason_code) == ("model", "auto_review_rejected")
    finally:
        store.close()


@pytest.mark.parametrize("outcome,risk", [("allow", "high"), ("deny", "unknown")])
def test_reserved_request_the_reviewer_does_not_decline_goes_to_the_user(tmp_path, outcome, risk):
    store, run, item = _pending_item(tmp_path)
    asked = []
    description = {"kind": "file_change", "requiresHumanConfirmation": True,
                   "riskWarning": "此操作会修改受保护路径。"}
    try:
        coordinator = _coordinator(store, _Reviewer(outcome, risk),
                                   lambda request, _cancel: asked.append(request) or ApprovalDecision("approve"))
        result = coordinator.request(run["id"], item, description, threading.Event(), transition_reason="file")
        assert result.decision == "approve" and len(asked) == 1
        assert asked[0]["requiresHumanConfirmation"] is True
        assert coordinator.last_review.source == "manual"
    finally:
        store.close()


def test_large_operation_evidence_is_previewed_instead_of_asking():
    diff = "+" + "x" * 200_000
    evidence = build_review_evidence(
        user_messages=("Rewrite the generated file",), tool="apply_patch",
        arguments=json.dumps({"patch": diff}), request={"kind": "file_change"},
        diff=diff, base_sha256=None,
    )
    assert len(evidence.encode()) <= MAX_REVIEW_INPUT_BYTES
    assert "Middle omitted for approval review" in evidence


def test_manual_network_approval_covers_later_commands_in_the_task(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    launches = []

    def start(_manager, launch, **_kwargs):
        launches.append(launch)
        return {"schemaVersion": 1, "toolName": "run_shell", "outcome": "success", "code": "ok",
                "summary": "done", "data": {"exitCode": 0, "stdout": "", "stderr": "",
                "truncated": False, "termination": "exit", "executionStatus": "exited",
                "workspaceChanged": False}, "sideEffectsMayExist": False, "reconciliationRequired": False}

    monkeypatch.setattr("eidos_runtime.runtime.shell_process_manager.ShellProcessManager.start", start)
    monkeypatch.setattr("eidos_runtime.runtime.tool_runtime.is_seatbelt_ready", lambda: True)
    asked = []
    shell = {"networkAccess": "request", "justification": "Install dependencies"}
    model = ScriptedModel([
        ModelResponse(tool_calls=(ModelToolCall("first", "run_shell", {"command": "npm install", **shell}),)),
        ModelResponse(tool_calls=(ModelToolCall("second", "run_shell", {"command": "npm test", **shell}),)),
        ModelResponse(text="done"),
    ])
    try:
        session = store.create_session(str(workspace))
        run, _ = store.create_run(session["id"], "Install and test", approval_mode="manual")
        RuntimeEngine(store, model, lambda _: None, shell_available=True,
                      request_approval=lambda request, _cancel: asked.append(request) or ApprovalDecision("approve"),
                      ).run(run["id"], threading.Event())
        assert len(launches) == 2
        assert len(asked) == 1 and asked[0]["grantScope"] == "run"
        stored = json.loads(store.connection.execute("SELECT request_json FROM approvals").fetchone()[0])
        assert stored["permissions"]["network"] == {"enabled": True}
    finally:
        store.close()


def test_skill_install_asks_once_without_a_network_grant(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        skills = SkillCatalog(PluginCatalog(store))
        session = store.create_session(str(workspace))
        run, _ = store.create_run(session["id"], "Install this skill",
                                  extension_snapshot=skills.extension_snapshot())
        asked = []
        monkeypatch.setattr("eidos_runtime.extensions.skills._download_github_skill", lambda _url, _cancel: (
            "example", {"SKILL.md": b"---\nname: example\ndescription: Example skill.\n---\nRead data.\n"}))
        model = ScriptedModel([
            ModelResponse(tool_calls=(ModelToolCall("install", "skill_install", {
                "url": "https://github.com/example/skills/tree/main/example"}),)),
            ModelResponse(text="Done."),
        ])
        RuntimeEngine(store, model, lambda _message: None,
                      request_approval=lambda request, _cancel: asked.append(request) or ApprovalDecision("approve"),
                      ).run(run["id"], threading.Event())
        assert [request["kind"] for request in asked] == ["file_change"]
        assert (store.data_directory / "skills" / "example" / "SKILL.md").exists()
    finally:
        store.close()


def test_task_scoped_tool_approval_is_bound_to_the_tool_and_active_run(tmp_path):
    store, run, item = _pending_item(tmp_path, "manual")
    content_hash = "a" * 64
    request = {"kind": "external_tool", "toolName": "mcp__tracker__create_issue", "grantScope": "run",
               "provenance": {"kind": "mcp", "sourceId": "p:tracker", "sourceVersion": "1", "contentHash": content_hash}}
    try:
        store.begin_approval_committed(str(item["id"]), "", None, request=request)
        assert not store.run_tool_granted(run["id"], "mcp__tracker__create_issue", content_hash)
        store.resolve_approval_committed(str(item["id"]), "approve", None)
        assert store.run_tool_granted(run["id"], "mcp__tracker__create_issue", content_hash)
        assert not store.run_tool_granted(run["id"], "mcp__tracker__delete_issue", content_hash)
        assert not store.run_tool_granted(run["id"], "mcp__tracker__create_issue", "b" * 64)
    finally:
        store.close()


def test_only_declared_destructive_mcp_tools_keep_per_call_approval():
    from mcp import types as mcp_types

    from eidos_runtime.extensions.mcp import _tool_entry

    connection = SimpleNamespace(config=SimpleNamespace(tool_timeout_seconds=30))
    server = {"serverId": "tracker", "pluginId": "p", "pluginVersion": "1"}

    def adapter(annotations):
        tool = mcp_types.Tool(name="change", annotations=annotations,
                              inputSchema={"type": "object", "properties": {"title": {"type": "string"}}})
        return _tool_entry(connection, tool, server).adapter

    assert adapter(mcp_types.ToolAnnotations(destructiveHint=True)).destructive is True
    assert adapter(mcp_types.ToolAnnotations(destructiveHint=False)).destructive is False
    assert adapter(None).destructive is False


def test_request_already_handed_to_the_user_stays_with_the_user_after_restart(tmp_path):
    class Crash(BaseException):
        pass

    def crash(_request, _cancel):
        raise Crash()

    store, run, item = _pending_item(tmp_path)
    asked = []
    try:
        # The reviewer allows a severe action, so the user is asked; then Runtime stops.
        with pytest.raises(Crash):
            _coordinator(store, _Reviewer("allow", "high"), crash).request(
                run["id"], item, {"kind": "file_change"}, threading.Event(), transition_reason="file")
        # A fresh review would now approve, but the user still decides.
        reviewer = _Reviewer("allow", "low")
        result = _coordinator(store, reviewer, lambda request, _cancel: asked.append(request) or ApprovalDecision("reject")).request(
            run["id"], item, {"kind": "file_change"}, threading.Event(), transition_reason="file")
        assert result.decision == "reject"
        assert reviewer.calls == 0 and len(asked) == 1 and asked[0]["reviewFallback"]
    finally:
        store.close()
