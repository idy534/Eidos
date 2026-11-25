from __future__ import annotations

from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from eidos_runtime.context.project_rules import ProjectRuleResolver
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.long_task import LongTaskStatus, ResumeOutcome, SafePoint
from eidos_runtime.runtime.supervisor import RunSupervisor


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _init_git_workspace(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    _git(workspace, "init", "-q", "-b", "main")
    _git(workspace, "config", "user.name", "Eidos Tests")
    _git(workspace, "config", "user.email", "eidos-tests@example.com")
    (workspace / "AGENTS.md").write_text("Initial project rule.\n", encoding="utf-8")
    (workspace / "tracked.txt").write_text("v1\n", encoding="utf-8")
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-qm", "initial")


def _insert_context_plan(
    store: SessionStore,
    *,
    run_id: str,
    plan_id: str,
    rule_snapshot_id: str,
    index_snapshot_id: str | None,
) -> None:
    with store.database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO context_plans (
                id, run_id, retrieval_snapshot_id, model_profile_snapshot_hash,
                rule_snapshot_id, inventory_snapshot_id, index_snapshot_id,
                snapshot_hash, plan_json, created_at
            ) VALUES (?, ?, NULL, ?, ?, NULL, ?, ?, '{}', 1)
            """,
            (
                plan_id,
                run_id,
                "profile-hash-1",
                rule_snapshot_id,
                index_snapshot_id,
                f"plan-hash-{plan_id}",
            ),
        )


class _FakeRepositoryRuntime:
    def __init__(self, index_snapshot_id: str) -> None:
        self.index_snapshot_id = index_snapshot_id

    def activate_workspace(self, root: Path) -> object:
        return self.available_for_run(root)

    def available_for_run(self, _root: Path) -> object:
        index_id = self.index_snapshot_id

        class _State:
            @staticmethod
            def capture_for_run() -> object:
                return SimpleNamespace(
                    snapshot=SimpleNamespace(index=SimpleNamespace(snapshot_id=index_id))
                )

        return _State()


def _make_supervisor(
    store: SessionStore, *, repository_runtime: object | None = None
) -> RunSupervisor:
    return RunSupervisor(
        store,
        lambda _run_id: None,  # type: ignore[arg-type,return-value]
        lambda _event: None,
        lambda value: value,
        lambda: True,
        lambda: True,
        lambda: None,
        repository_runtime=repository_runtime,  # type: ignore[arg-type]
    )


def _setup_paused_task(
    tmp_path: Path, *, repository_runtime: object | None = None
) -> tuple[SessionStore, Path, str, RunSupervisor]:
    workspace = tmp_path / "workspace"
    _init_git_workspace(workspace)
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.create_session(str(workspace))
    run, _ = store.create_run(str(session["id"]), "inspect workspace")
    run_id = str(run["id"])
    supervisor = _make_supervisor(store, repository_runtime=repository_runtime)
    supervisor._initialize_long_task(run_id)
    rule_id = ProjectRuleResolver().resolve(workspace, workspace).id
    _insert_context_plan(
        store,
        run_id=run_id,
        plan_id="plan-1",
        rule_snapshot_id=rule_id,
        index_snapshot_id="index-1",
    )
    tasks = store.long_task_repository()
    tasks.record_snapshots(
        run_id,
        rule_snapshot_id=rule_id,
        index_snapshot_id="index-1",
        context_plan_id="plan-1",
    )
    tasks.request_pause(run_id)
    tasks.mark_paused(run_id, SafePoint.BEFORE_MODEL)
    return store, workspace, run_id, supervisor


def test_unchanged_state_allows_safe_resume_on_restart_and_resume(tmp_path: Path) -> None:
    repo_runtime = _FakeRepositoryRuntime("index-1")
    store, _workspace, run_id, supervisor = _setup_paused_task(
        tmp_path, repository_runtime=repo_runtime
    )
    try:
        restarted = supervisor.verify_restart_state()
        assert len(restarted) == 1
        assert restarted[0].last_verification is not None
        assert restarted[0].last_verification.outcome is ResumeOutcome.SAFE_RESUME
        assert restarted[0].last_verification.reasons == ()

        resumed = supervisor.resume_run(run_id)
        assert resumed.status is LongTaskStatus.RUNNING
        assert resumed.last_verification is not None
        assert resumed.last_verification.outcome is ResumeOutcome.SAFE_RESUME
        assert resumed.last_verification.reasons == ()
    finally:
        store.close()


def test_git_head_commit_change_triggers_workspace_changed_on_restart_and_resume(
    tmp_path: Path,
) -> None:
    store, workspace, run_id, supervisor = _setup_paused_task(tmp_path)
    try:
        (workspace / "tracked.txt").write_text("v2\n", encoding="utf-8")
        _git(workspace, "add", "tracked.txt")
        _git(workspace, "commit", "-qm", "advance head")

        restarted = supervisor.verify_restart_state()
        assert restarted[0].last_verification is not None
        assert restarted[0].last_verification.outcome is ResumeOutcome.WORKSPACE_CHANGED
        assert "git_head_changed" in restarted[0].last_verification.reasons

        resumed = supervisor.resume_run(run_id)
        assert resumed.status is LongTaskStatus.RESUME_REQUESTED
        assert resumed.last_verification is not None
        assert resumed.last_verification.outcome is ResumeOutcome.WORKSPACE_CHANGED
        assert "git_head_changed" in resumed.last_verification.reasons
    finally:
        store.close()


def test_rule_file_change_triggers_rebuild_context_on_restart_and_resume(
    tmp_path: Path,
) -> None:
    store, workspace, run_id, supervisor = _setup_paused_task(tmp_path)
    try:
        (workspace / "AGENTS.md").write_text("Modified rule!\n", encoding="utf-8")

        restarted = supervisor.verify_restart_state()
        assert restarted[0].last_verification is not None
        assert restarted[0].last_verification.outcome is ResumeOutcome.REBUILD_CONTEXT
        assert "rule_snapshot_changed" in restarted[0].last_verification.reasons

        resumed = supervisor.resume_run(run_id)
        assert resumed.status is LongTaskStatus.RESUME_REQUESTED
        assert resumed.last_verification is not None
        assert resumed.last_verification.outcome is ResumeOutcome.REBUILD_CONTEXT
        assert "rule_snapshot_changed" in resumed.last_verification.reasons
    finally:
        store.close()


def test_newer_context_plan_row_triggers_rebuild_context_on_restart_and_resume(
    tmp_path: Path,
) -> None:
    store, workspace, run_id, supervisor = _setup_paused_task(tmp_path)
    try:
        rule_id = ProjectRuleResolver().resolve(workspace, workspace).id
        _insert_context_plan(
            store,
            run_id=run_id,
            plan_id="plan-2",
            rule_snapshot_id=rule_id,
            index_snapshot_id="index-1",
        )

        restarted = supervisor.verify_restart_state()
        assert restarted[0].last_verification is not None
        assert restarted[0].last_verification.outcome is ResumeOutcome.REBUILD_CONTEXT
        assert "context_plan_changed" in restarted[0].last_verification.reasons

        resumed = supervisor.resume_run(run_id)
        assert resumed.status is LongTaskStatus.RESUME_REQUESTED
        assert resumed.last_verification is not None
        assert resumed.last_verification.outcome is ResumeOutcome.REBUILD_CONTEXT
        assert "context_plan_changed" in resumed.last_verification.reasons
    finally:
        store.close()


@pytest.mark.parametrize("source", ["repository_runtime", "persisted_plan"])
def test_index_snapshot_change_triggers_reindex_required_on_restart_and_resume(
    tmp_path: Path, source: str
) -> None:
    repo_runtime = _FakeRepositoryRuntime("index-1") if source == "repository_runtime" else None
    store, workspace, run_id, supervisor = _setup_paused_task(
        tmp_path, repository_runtime=repo_runtime
    )
    try:
        if source == "repository_runtime":
            assert repo_runtime is not None
            repo_runtime.index_snapshot_id = "index-2"
        else:
            rule_id = ProjectRuleResolver().resolve(workspace, workspace).id
            _insert_context_plan(
                store,
                run_id=run_id,
                plan_id="plan-1-reindexed",
                rule_snapshot_id=rule_id,
                index_snapshot_id="index-2",
            )
            # Keep context_plan_id assertion isolated or ensure index_snapshot_changed wins priority
            with store.database.transaction() as connection:
                connection.execute(
                    "UPDATE context_plans SET index_snapshot_id = 'index-2' WHERE id = 'plan-1'"
                )

        restarted = supervisor.verify_restart_state()
        assert restarted[0].last_verification is not None
        assert restarted[0].last_verification.outcome is ResumeOutcome.REINDEX_REQUIRED
        assert "index_snapshot_changed" in restarted[0].last_verification.reasons

        resumed = supervisor.resume_run(run_id)
        assert resumed.status is LongTaskStatus.RESUME_REQUESTED
        assert resumed.last_verification is not None
        assert resumed.last_verification.outcome is ResumeOutcome.REINDEX_REQUIRED
        assert "index_snapshot_changed" in resumed.last_verification.reasons
    finally:
        store.close()


def test_approval_mode_or_grant_change_triggers_permission_changed(tmp_path: Path) -> None:
    store, _workspace, run_id, supervisor = _setup_paused_task(tmp_path)
    try:
        with store.database.transaction() as connection:
            connection.execute(
                "UPDATE runs SET approval_mode = 'auto_review' WHERE id = ?",
                (run_id,),
            )

        restarted = supervisor.verify_restart_state()
        assert restarted[0].last_verification is not None
        assert restarted[0].last_verification.outcome is ResumeOutcome.PERMISSION_CHANGED
        assert "permission_snapshot_changed" in restarted[0].last_verification.reasons

        resumed = supervisor.resume_run(run_id)
        assert resumed.status is LongTaskStatus.RESUME_REQUESTED
        assert resumed.last_verification is not None
        assert resumed.last_verification.outcome is ResumeOutcome.PERMISSION_CHANGED
        assert "permission_snapshot_changed" in resumed.last_verification.reasons
    finally:
        store.close()


def test_approved_run_grant_change_triggers_permission_changed(tmp_path: Path) -> None:
    store, workspace, run_id, supervisor = _setup_paused_task(tmp_path)
    try:
        extra = (tmp_path / "extra-dir").resolve()
        extra.mkdir()
        item = store.create_tool_item(run_id, 1, 0, "perm-1", "request_permissions", "{}")
        request_payload = {
            "kind": "permission_request",
            "grantScope": "run",
            "permissions": {
                "fileSystem": [
                    {"path": str(extra), "access": "write", "recursive": True}
                ]
            },
        }
        store.begin_approval(
            str(item["id"]),
            "",
            None,
            request=request_payload,
            approval_kind="additional_permissions",
        )
        store.resolve_approval(str(item["id"]), "approve", None)

        restarted = supervisor.verify_restart_state()
        assert restarted[0].last_verification is not None
        assert restarted[0].last_verification.outcome is ResumeOutcome.PERMISSION_CHANGED
        assert "permission_snapshot_changed" in restarted[0].last_verification.reasons

        resumed = supervisor.resume_run(run_id)
        assert resumed.status is LongTaskStatus.RESUME_REQUESTED
        assert resumed.last_verification is not None
        assert resumed.last_verification.outcome is ResumeOutcome.PERMISSION_CHANGED
        assert "permission_snapshot_changed" in resumed.last_verification.reasons
    finally:
        store.close()


def test_pending_approval_triggers_approval_required_on_restart_and_resume(
    tmp_path: Path,
) -> None:
    store, _workspace, run_id, supervisor = _setup_paused_task(tmp_path)
    try:
        item = store.create_tool_item(run_id, 1, 0, "call-1", "run_command", "{}")
        store.begin_approval(
            str(item["id"]),
            "",
            None,
            request={"kind": "command_execution"},
            approval_kind="tool",
        )

        restarted = supervisor.verify_restart_state()
        assert restarted[0].last_verification is not None
        assert restarted[0].last_verification.outcome is ResumeOutcome.APPROVAL_REQUIRED
        assert "approval_required" in restarted[0].last_verification.reasons

        resumed = supervisor.resume_run(run_id)
        assert resumed.status is LongTaskStatus.RESUME_REQUESTED
        assert resumed.last_verification is not None
        assert resumed.last_verification.outcome is ResumeOutcome.APPROVAL_REQUIRED
        assert "approval_required" in resumed.last_verification.reasons
    finally:
        store.close()
