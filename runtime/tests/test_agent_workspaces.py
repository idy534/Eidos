from __future__ import annotations

import sqlite3
import subprocess
import threading
from pathlib import Path

import pytest

from eidos_runtime.application.agent_workspaces import AgentWorkspaceService
from eidos_runtime.application.collaboration import CollaborationApplication
from eidos_runtime.application.session_lifecycle import SessionLifecycleCoordinator
from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.application.worktree_retention import WorktreeRetentionService
from eidos_runtime.db.repositories.sessions import SessionRepository
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.collaboration import CollaborationRejected, SpawnAgent
from eidos_runtime.git.manager import WorktreeManager
from eidos_runtime.protocol.methods import SessionCreateRequestDto


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def test_v54_upgrade_adds_child_workspace_schema_and_keeps_defaults(tmp_path):
    from schema_fixtures import remove_agent_workspace_schema

    from eidos_runtime.db.schema import SCHEMA_VERSION

    data = tmp_path / "data"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(data)
    store.initialize()
    store.create_session(str(workspace))
    store.close()
    with sqlite3.connect(data / "state.sqlite") as connection:
        remove_agent_workspace_schema(connection)
        connection.execute("PRAGMA user_version=54")
    reopened = SessionStore(data)
    reopened.initialize()
    try:
        connection = reopened.connection
        assert reopened.health()["state"] == "ready"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert {"workspace_mode", "worktree_id", "base_tree"} <= _columns(connection, "agent_delegations")
        assert {"sender_kind", "operation_id"} <= _columns(connection, "agent_messages")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "workspace_deliveries" in tables
        defaults = {row[1]: row[4] for row in connection.execute("PRAGMA table_info(agent_delegations)")}
        assert defaults["workspace_mode"] == "'shared'"
    finally:
        reopened.close()


def test_agent_workspace_migration_failure_rolls_back(tmp_path, monkeypatch):
    from schema_fixtures import remove_agent_workspace_schema

    from eidos_runtime.db import agent_workspace_migration
    from eidos_runtime.db.schema import SCHEMA_SQL

    with sqlite3.connect(tmp_path / "state.sqlite") as connection:
        connection.executescript(SCHEMA_SQL)
        remove_agent_workspace_schema(connection)
        connection.execute("PRAGMA user_version=54")
        connection.commit()
        monkeypatch.setattr(
            agent_workspace_migration,
            "AGENT_WORKSPACE_SCHEMA_SQL",
            agent_workspace_migration.AGENT_WORKSPACE_SCHEMA_SQL + "SELECT nonexistent_column FROM agent_messages;",
        )
        with pytest.raises(sqlite3.OperationalError):
            agent_workspace_migration.migrate_agent_workspaces(connection)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 54
        assert "workspace_mode" not in _columns(connection, "agent_delegations")
        assert "sender_kind" not in _columns(connection, "agent_messages")
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='workspace_deliveries'"
        ).fetchone() is None


# Child agent Worktrees ----------------------------------------------------------

def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


class Parent:
    def __init__(self, tmp_path: Path, execution_mode: str = "local") -> None:
        root = tmp_path / "repository"
        root.mkdir()
        git(root, "init", "-q", "-b", "main")
        git(root, "config", "user.name", "Eidos Tests")
        git(root, "config", "user.email", "tests@example.com")
        (root / "a.txt").write_text("one\ntwo\nthree\nfour\nfive\n")
        (root / "b.txt").write_text("remove me\n")
        git(root, "add", ".")
        git(root, "commit", "-qm", "base")
        self.store = SessionStore(tmp_path / "data")
        self.store.initialize()
        self.manager = WorktreeManager(self.store.database, managed_root=tmp_path / "managed")
        self.retention = WorktreeRetentionService(self.store.database, self.manager)
        self.sessions = SessionApplication(self.store, scan_text=lambda text: text, worktree_manager=self.manager,
                                           retention=self.retention, lifecycle=SessionLifecycleCoordinator())
        self.session = self.sessions.create(SessionCreateRequestDto(workspaceRoot=str(root), executionMode=execution_mode)).root
        self.root = Path(self.session["worktree"]["worktreeRoot"]) if execution_mode == "worktree" else root
        self.run, _ = self.store.create_run(self.session["id"], "coordinate isolated work")
        self.workspaces = AgentWorkspaceService(self.store.database, self.manager, self.retention.snapshot_service)
        self.app = CollaborationApplication(self.store, lambda: None, lambda _run: None, lambda: None)
        self.app.bind_workspaces(self.workspaces)

    def spawn(self, index: int = 0, workspace: str = "worktree"):
        item = self.store.create_tool_item(self.run["id"], 1, index, f"spawn-{index}", "spawn_agent", "{}")
        request = SpawnAgent(task_name=f"edit-{index}", message="Edit the files.", role="worker", workspace=workspace)
        return item["id"], request, self.app.spawn(self.run["id"], item["id"], request)

    def child_root(self, agent) -> Path:
        row = self.store.connection.execute("SELECT worktree_id FROM agent_delegations WHERE id=?", (agent.id,)).fetchone()
        return Path(self.manager.read_worktree(row[0]).worktree_root)

    def finish(self, agent) -> None:
        claimed = self.store.claim_next_run_committed()
        assert claimed is not None and claimed.value["id"] == agent.run_id
        self.store.fail_run(agent.run_id, "fixture_done")

    def integrate(self, agent, **arguments):
        from eidos_runtime.tools.agent_integration import agent_integration_entry
        from eidos_runtime.tools.runtime_workspace import ToolExecutor

        with ToolExecutor(self.root) as executor:
            adapter = agent_integration_entry(executor, self.workspaces, self.run["id"]).adapter
            prepared = adapter.prepare_file_change({"agentId": agent.id, **arguments}, threading.Event())
            if isinstance(prepared, dict):
                return prepared
            return adapter.commit_patch(prepared, threading.Event())[0]


@pytest.mark.parametrize("execution_mode", ["local", "worktree"])
def test_worktree_child_starts_from_uncommitted_parent_state_and_spawn_replays(tmp_path, execution_mode):
    parent = Parent(tmp_path, execution_mode)
    try:
        (parent.root / "a.txt").write_text("one\ntwo\nthree\nfour\nfive\nsix\n")
        (parent.root / "c.txt").write_text("untracked\n")
        before = git(parent.root, "status", "--porcelain")
        item_id, request, child = parent.spawn()
        child_root = parent.child_root(child)
        assert child_root != parent.root
        assert (child_root / "a.txt").read_text().endswith("six\n")
        assert (child_root / "c.txt").read_text() == "untracked\n"
        assert git(parent.root, "status", "--porcelain") == before
        assert parent.app.spawn(parent.run["id"], item_id, request).id == child.id
        row = parent.store.connection.execute(
            "SELECT workspace_mode,base_tree,child_session_id FROM agent_delegations WHERE id=?", (child.id,)).fetchone()
        assert row[0] == "worktree"
        assert git(parent.root, "rev-parse", f"refs/eidos/agent-workspaces/{child.id}/base") == row[1]
        assert parent.store.read_session(row[2]) is not None
        assert parent.store.connection.execute(
            "SELECT execution_mode FROM sessions WHERE id=?", (row[2],)).fetchone()[0] == "worktree"
        assert not parent.manager.lifecycle.list_unfinished()
    finally:
        parent.store.close()


def test_delivery_merges_parent_edits_and_reports_conflicts_without_writing(tmp_path):
    parent = Parent(tmp_path)
    try:
        _item, _request, child = parent.spawn()
        child_root = parent.child_root(child)
        (child_root / "a.txt").write_text("one\ntwo\nthree\nfour\nFIVE\n")
        (child_root / "d.txt").write_text("added by child\n")
        (child_root / "b.txt").unlink()
        with pytest.raises(Exception):
            parent.workspaces.capture(parent.run["id"], child.id)
        parent.finish(child)
        delivery = parent.workspaces.capture(parent.run["id"], child.id)
        assert {(entry.path, entry.kind) for entry in delivery.entries} == {
            ("a.txt", "modified"), ("d.txt", "added"), ("b.txt", "deleted")}
        (parent.root / "a.txt").write_text("ONE\ntwo\nthree\nfour\nfive\n")
        result = parent.integrate(child)
        assert result["outcome"] == "success", result
        assert (parent.root / "a.txt").read_text() == "ONE\ntwo\nthree\nfour\nFIVE\n"
        assert (parent.root / "d.txt").read_text() == "added by child\n"
        assert not (parent.root / "b.txt").exists()
        assert parent.workspaces.latest_delivery(child.id).status == "integrated"
        assert parent.integrate(child)["code"] == "agent_changes_already_integrated"

        _item, _request, second = parent.spawn(1)
        (parent.child_root(second) / "a.txt").write_text("conflict from child\n")
        parent.finish(second)
        (parent.root / "a.txt").write_text("conflict from parent\n")
        blocked = parent.integrate(second)
        assert blocked["code"] == "agent_changes_conflict" and "a.txt (conflict)" in blocked["summary"]
        assert (parent.root / "a.txt").read_text() == "conflict from parent\n"
    finally:
        parent.store.close()


def test_discard_and_parent_delete_remove_child_worktrees_and_block_restart(tmp_path):
    parent = Parent(tmp_path)
    try:
        _item, _request, child = parent.spawn()
        child_root = parent.child_root(child)
        (child_root / "a.txt").write_text("changed\n")
        parent.finish(child)
        parent.workspaces.discard(parent.run["id"], child.id)
        assert not child_root.exists()
        item = parent.store.create_tool_item(parent.run["id"], 2, 0, "follow", "followup_task", "{}")
        with pytest.raises(CollaborationRejected, match="agent_workspace_discarded"):
            parent.app.repository.followup(parent.run["id"], item["id"], child.id, "Continue")

        _item, _request, kept = parent.spawn(1)
        kept_root = parent.child_root(kept)
        (kept_root / "a.txt").write_text("kept change\n")
        parent.finish(kept)
        parent.workspaces.capture(parent.run["id"], kept.id)
        parent.workspaces.release_for_session(parent.session["id"])
        assert not kept_root.exists()
        parent.store.fail_run(parent.run["id"], "fixture_done")
        SessionRepository(parent.store.database).delete_session_committed(parent.session["id"])
        assert parent.store.connection.execute("SELECT COUNT(*) FROM workspace_deliveries").fetchone()[0] == 0
    finally:
        parent.store.close()


def test_retention_skips_child_worktrees_and_startup_drops_uncommitted_spawns(tmp_path):
    parent = Parent(tmp_path)
    try:
        _item, _request, child = parent.spawn()
        row = parent.store.connection.execute("SELECT worktree_id FROM agent_delegations WHERE id=?", (child.id,)).fetchone()
        assert parent.retention._protected_reason(parent.manager.read_worktree(row[0])) == "agent_workspace"

        orphan = parent.workspaces.prepare(parent.session["id"], "never-committed")
        orphan_root = Path(parent.manager.read_worktree(orphan.worktree_id).worktree_root)
        assert orphan_root.exists()
        parent.workspaces.reconcile()
        assert not orphan_root.exists()
        assert parent.child_root(child).exists()
        assert not parent.manager.lifecycle.list_unfinished()
    finally:
        parent.store.close()


def test_worktree_spawn_requires_git(tmp_path):
    workspace = tmp_path / "plain"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        manager = WorktreeManager(store.database, managed_root=tmp_path / "managed")
        retention = WorktreeRetentionService(store.database, manager)
        session = store.create_session(str(workspace))
        run, _ = store.create_run(session["id"], "coordinate")
        app = CollaborationApplication(store, lambda: None, lambda _run: None, lambda: None)
        app.bind_workspaces(AgentWorkspaceService(store.database, manager, retention.snapshot_service))
        item = store.create_tool_item(run["id"], 1, 0, "spawn", "spawn_agent", "{}")
        with pytest.raises(CollaborationRejected, match="agent_worktree_requires_git"):
            app.spawn(run["id"], item["id"], SpawnAgent(task_name="edit", message="Edit.", workspace="worktree"))
        assert store.connection.execute("SELECT COUNT(*) FROM agent_delegations").fetchone()[0] == 0
    finally:
        store.close()


def test_review_changes_lists_files_and_pages_diff_without_object_ids(tmp_path):
    from eidos_runtime.domain.collaboration import ReviewAgentChanges

    parent = Parent(tmp_path)
    try:
        _item, _request, child = parent.spawn()
        (parent.child_root(child) / "a.txt").write_text("one\ntwo\nthree\nfour\nFIVE\n")
        with pytest.raises(CollaborationRejected, match="agent_still_running"):
            parent.app.review_changes(parent.run["id"], ReviewAgentChanges(agent_id=child.id))
        parent.finish(child)
        changes = parent.app.review_changes(parent.run["id"], ReviewAgentChanges(agent_id=child.id, max_bytes=64))
        assert [(item.path, item.kind, item.additions, item.deletions) for item in changes.files] == [("a.txt", "modified", 1, 1)]
        assert changes.diff.startswith("diff --git") and changes.diff_has_more
        rest = parent.app.review_changes(parent.run["id"], ReviewAgentChanges(
            agent_id=child.id, offset_bytes=changes.diff_next_offset))
        assert "+FIVE" in changes.diff + rest.diff and not rest.diff_has_more
        dumped = changes.model_dump_json()
        row = parent.store.connection.execute("SELECT base_tree FROM agent_delegations WHERE id=?", (child.id,)).fetchone()
        assert row[0] not in dumped
        with pytest.raises(CollaborationRejected, match="agent_change_path_unknown"):
            parent.app.review_changes(parent.run["id"], ReviewAgentChanges(agent_id=child.id, path="missing.txt"))
    finally:
        parent.store.close()


def test_v55_upgrade_adds_team_schema_and_marks_existing_children(tmp_path):
    from schema_fixtures import remove_team_schema

    from eidos_runtime.db.schema import SCHEMA_VERSION

    data = tmp_path / "data"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(data)
    store.initialize()
    session = store.create_session(str(workspace))
    run, _ = store.create_run(session["id"], "coordinate")
    item = store.create_tool_item(run["id"], 1, 0, "spawn", "spawn_agent", "{}")
    from eidos_runtime.persistence.collaboration import CollaborationRepository
    child = CollaborationRepository(store.database).spawn(run["id"], item["id"], SpawnAgent(task_name="look", message="Look."))
    store.close()
    with sqlite3.connect(data / "state.sqlite") as connection:
        remove_team_schema(connection)
        connection.executescript("""
CREATE TABLE workspace_integrations (id TEXT PRIMARY KEY);
CREATE INDEX workspace_integrations_delivery ON workspace_integrations(id);
""")
        connection.execute("PRAGMA user_version=55")
    reopened = SessionStore(data)
    reopened.initialize()
    try:
        connection = reopened.connection
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("SELECT internal_kind FROM sessions WHERE id=?", (child.session_id,)).fetchone()[0] == "agent_child"
        assert connection.execute("SELECT internal_kind FROM sessions WHERE id=?", (session["id"],)).fetchone()[0] is None
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"teams", "team_members", "team_tasks", "team_messages", "team_wakes"} <= tables
        assert "workspace_integrations" not in tables
    finally:
        reopened.close()
