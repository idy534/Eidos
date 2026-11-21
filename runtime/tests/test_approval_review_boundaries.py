"""Auto review keeps the reserved human decisions and the user's own words."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import threading

import pytest

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.model.config import default_profile_snapshot
from eidos_runtime.runtime.approval import ApprovalDecision
from eidos_runtime.runtime.approval_review import (
    MAX_REVIEW_INPUT_BYTES,
    MAX_USER_EVIDENCE_BYTES,
    bounded_user_evidence,
)
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.runtime.shell_orchestration import (
    ShellReviewScript,
    references_core_protected_scope,
)
from eidos_runtime.sandbox.permissions import base_permission_profile_for_workspace


@pytest.fixture
def home_layout(tmp_path, monkeypatch):
    home = tmp_path.resolve() / "home"
    data, workspace = home / ".eidos", home / "proj"
    (data / "skills" / ".system").mkdir(parents=True)
    workspace.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home, data, workspace


@pytest.mark.parametrize("command,cwd", [
    ("sqlite3 {data}/state.db .dump", "workspace"),
    ("sqlite3 ~/.eidos/state.db .dump", "workspace"),
    ('cp "$HOME/.eidos/state.db" /tmp/copy', "workspace"),
    ("tar czf /tmp/all.tgz ${{HOME}}/.eidos", "workspace"),
    ("rm -rf ~", "workspace"),
    ("du -sh ~/", "workspace"),
    ("cat .eidos/state.db", "home"),
    ("python3 -c 'open(\"{data}/skills/.system/SKILL.md\", \"w\")'", "workspace"),
])
def test_unsandboxed_commands_naming_core_scopes_are_detected(home_layout, command, cwd):
    home, data, workspace = home_layout
    base = base_permission_profile_for_workspace(workspace, data)
    directory = {"workspace": workspace, "home": home}[cwd]

    assert references_core_protected_scope(command.format(data=data), directory, base)


@pytest.mark.parametrize("command", [
    "npm test",
    "git status && git diff",
    "cat ~/proj/README.md",
    "ls {workspace}/src > /dev/null",
    "curl -s https://example.com/api/v1 | jq .",
])
def test_ordinary_unsandboxed_commands_stay_with_auto_review(home_layout, command):
    _home, data, workspace = home_layout
    base = base_permission_profile_for_workspace(workspace, data)

    assert not references_core_protected_scope(command.format(workspace=workspace), workspace, base)


def test_projectless_workspace_inside_data_is_not_core(home_layout):
    _home, data, _workspace = home_layout
    projectless = data / "..eidos-projectless" / "session"
    projectless.mkdir(parents=True)
    base = base_permission_profile_for_workspace(projectless, data)

    assert not references_core_protected_scope(f"make -C {projectless} build", projectless, base)
    assert references_core_protected_scope(f"cat {data}/state.db", projectless, base)


def test_script_preview_that_names_core_scope_is_detected(home_layout):
    _home, data, workspace = home_layout
    base = base_permission_profile_for_workspace(workspace, data)
    script = ShellReviewScript(path=str(workspace / "clean.sh"), content="rm -rf ~/.eidos/skills\n")

    assert references_core_protected_scope("sh ./clean.sh", workspace, base, (script,))


@pytest.mark.skipif(sys.platform not in {"darwin", "win32"}, reason="case-insensitive host paths")
def test_case_variant_of_core_scope_is_detected(home_layout):
    _home, data, workspace = home_layout
    base = base_permission_profile_for_workspace(workspace, data)

    assert references_core_protected_scope("cat ~/.EIDOS/state.db", workspace, base)


def test_user_evidence_keeps_newest_messages_within_budget():
    messages = tuple(f"message {index} " + "x" * 60_000 + f" end {index}" for index in range(32))

    bounded = bounded_user_evidence(messages)

    assert sum(len(message.encode()) for message in bounded) <= MAX_USER_EVIDENCE_BYTES + 200
    assert bounded[-1].startswith("message 31") and bounded[-1].endswith("end 31")
    assert "earlier user messages omitted" in bounded[0]
    assert MAX_USER_EVIDENCE_BYTES < MAX_REVIEW_INPUT_BYTES // 2


def test_short_user_evidence_is_unchanged():
    assert bounded_user_evidence(("Fix the build", "Also push it")) == ["Fix the build", "Also push it"]


def _shell_model(command: str, assessment: dict[str, object] | None) -> ScriptedModel:
    responses = [ModelResponse(tool_calls=(ModelToolCall("shell", "run_shell", {
        "command": command, "sandboxPermissions": "require_escalated",
        "justification": "Inspect local state",
    }),))]
    if assessment is not None:
        responses.append(ModelResponse(tool_calls=(ModelToolCall(
            "assessment", "submit_approval_assessment", assessment,
        ),)))
    responses.append(ModelResponse(text="done"))
    model = ScriptedModel(responses)
    model.profile_snapshot = default_profile_snapshot("deepseek-v4-flash")
    return model


def _launches(monkeypatch) -> list[object]:
    launches: list[object] = []

    def start(_manager, launch, **_kwargs):
        launches.append(launch)
        return {"schemaVersion": 1, "toolName": "run_shell", "outcome": "success", "code": "ok",
                "summary": "done", "data": {"exitCode": 0, "stdout": "", "stderr": "",
                "truncated": False, "termination": "exit", "executionStatus": "exited",
                "workspaceChanged": False}, "sideEffectsMayExist": False, "reconciliationRequired": False}

    monkeypatch.setattr("eidos_runtime.runtime.shell_process_manager.ShellProcessManager.start", start)
    return launches


def test_auto_review_unsandboxed_command_on_private_data_needs_the_user(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    launches = _launches(monkeypatch)
    prompts = []

    def human(request, _cancel):
        prompts.append(request)
        return ApprovalDecision("reject", "Leave the Eidos database alone.")

    try:
        session = store.create_session(str(workspace))
        run, _ = store.create_run(session["id"], "Check my notes", approval_mode="auto_review")
        command = f"sqlite3 {(tmp_path / 'data').resolve()}/state.db .tables"
        # No reviewer response is scripted: the reviewer must not be asked.
        RuntimeEngine(store, _shell_model(command, None), lambda _: None,
                      request_approval=human, shell_available=True).run(run["id"], threading.Event())

        assert launches == []
        assert len(prompts) == 1 and prompts[0]["requiresHumanConfirmation"] is True
        row = store.connection.execute("SELECT status, feedback, review_json FROM approvals").fetchone()
        assert row["status"] == "rejected"
        assert row["feedback"] == "Leave the Eidos database alone."
        assert json.loads(row["review_json"])["source"] == "manual"
        result = json.loads(store.connection.execute("SELECT result_json FROM tool_calls").fetchone()[0])
        assert result["code"] == "user_rejected"
        assert result["summary"] == "Leave the Eidos database alone."
    finally:
        store.close()


@pytest.mark.parametrize("risk,expected_prompts", [("medium", 0), ("high", 1), ("critical", 1)])
def test_reviewer_allow_with_severe_risk_goes_to_the_user(tmp_path, monkeypatch, risk, expected_prompts):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "render.sh").write_text("printf ready\n")
    store = SessionStore(tmp_path / "data")
    store.initialize()
    launches = _launches(monkeypatch)
    prompts = []

    def human(request, _cancel):
        prompts.append(request)
        return ApprovalDecision("approve")

    try:
        session = store.create_session(str(workspace))
        run, _ = store.create_run(session["id"], "Render my document", approval_mode="auto_review")
        model = _shell_model("sh ./render.sh", {
            "outcome": "allow", "risk": risk, "rationale": "The user asked for this render.",
        })
        RuntimeEngine(store, model, lambda _: None, request_approval=human,
                      shell_available=True).run(run["id"], threading.Event())

        assert len(prompts) == expected_prompts
        assert len(launches) == 1
        review = json.loads(store.connection.execute("SELECT review_json FROM approvals").fetchone()[0])
        if expected_prompts:
            assert review["source"] == "manual"
            assert review["reason_code"] == "auto_review_requires_human"
            assert "reviewFallback" in prompts[0]
        else:
            assert review["source"] == "model"
    finally:
        store.close()


def _system_skill_run(tmp_path: Path, mode: str):
    data, workspace = tmp_path / "data", tmp_path / "workspace"
    data.mkdir(mode=0o700)
    workspace.mkdir()
    store = SessionStore(data)
    store.initialize()
    session = store.create_session(str(workspace))
    run, _ = store.create_run(session["id"], "Update the system skill", approval_mode=mode)
    target = data / "skills" / ".system" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("old\n")
    triage = ModelResponse(tool_calls=(ModelToolCall("triage", "submit_approval_assessment", {
        "outcome": "allow", "risk": "high", "rationale": "The user asked to update the system skill."}),))
    model = ScriptedModel([
        ModelResponse(tool_calls=(ModelToolCall("edit", "apply_patch", {"patch":
            f"*** Begin Patch\n*** Update File: {target}\n@@\n-old\n+new\n*** End Patch"}),)),
        *((triage,) if mode == "auto_review" else ()),
        ModelResponse(text="done"),
    ])
    model.profile_snapshot = default_profile_snapshot("deepseek-v4-flash")
    return store, run, model, target


@pytest.mark.parametrize("mode", ["manual", "auto_review"])
def test_high_risk_rejection_returns_the_users_feedback(tmp_path, monkeypatch, mode):
    store, run, model, target = _system_skill_run(tmp_path, mode)
    monkeypatch.setattr("eidos_runtime.runtime.tool_runtime.is_seatbelt_ready", lambda: False)
    feedback = "Do not touch system skills; write docs/notes.md instead."
    try:
        RuntimeEngine(store, model, lambda _: None,
                      request_approval=lambda _request, _cancel: ApprovalDecision("reject", feedback),
                      ).run(run["id"], threading.Event())
        assert target.read_text() == "old\n"
        row = store.connection.execute("SELECT status, feedback FROM approvals").fetchone()
        assert (row["status"], row["feedback"]) == ("rejected", feedback)
        result = json.loads(store.connection.execute("SELECT result_json FROM tool_calls").fetchone()[0])
        assert result["code"] == "user_rejected"
        assert result["summary"] == feedback
    finally:
        store.close()
