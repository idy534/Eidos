"""Managed Worktrees owned by child agents: creation, deliveries and removal.

A child created with ``workspace="worktree"`` starts from a snapshot of the
parent workspace, including uncommitted changes. Runtime pins that starting
state as a Git tree. A delivery pins the child's current files as another tree;
the parent integrates it through its own file-change tool, so writes keep the
parent Run's permissions, Durable Intent and version checks.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field

from eidos_runtime.application.worktree_snapshots import WorktreeSnapshotService
from eidos_runtime.db.database import Database, now_ms
from eidos_runtime.db.repositories.sessions import SessionRepository
from eidos_runtime.domain.worktree import (
    WorktreeLifecycleOperation,
    WorktreeLifecycleScope,
    WorktreeLifecycleState,
    WorktreeState,
)
from eidos_runtime.git.errors import GitError, WorktreeError
from eidos_runtime.git.manager import WorktreeManager
from eidos_runtime.git.native import GitCli
from eidos_runtime.models import EidosFrozenStrictModel, JsonSafeInt
from eidos_runtime.persistence.project_workspaces import project_for_session
from eidos_runtime.sandbox.file_commit_helper import MAX_FILE_BYTES

OPERATION_PREFIX = "agent-workspace-"
MAX_DELIVERY_FILES = 2_000
MAX_DELIVERY_BYTES = 64 * 1024 * 1024
MAX_DIFF_PAGE_BYTES = 64 * 1024

logger = logging.getLogger("eidos.runtime")


class AgentWorkspaceError(Exception):
    """A stable, model-visible reason; never a Git or filesystem message."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class DeliveryEntry(EidosFrozenStrictModel):
    path: str
    kind: Literal["added", "modified", "deleted"]
    base: str | None = None
    result: str | None = None
    mode: int = Field(ge=0)
    binary: bool = False
    additions: JsonSafeInt = Field(default=0, ge=0)
    deletions: JsonSafeInt = Field(default=0, ge=0)


class Delivery(EidosFrozenStrictModel):
    id: str
    agent_id: str
    worktree_id: str
    base_tree: str
    result_tree: str
    entries: tuple[DeliveryEntry, ...]
    status: Literal["ready", "stale", "integrated", "partially_integrated", "discarded"]


@dataclass(frozen=True)
class PreparedAgentWorkspace:
    operation_id: str
    session_id: str
    worktree_id: str
    base_tree: str


def child_session_id(spawn_item_id: str) -> str:
    """A spawn retry must bind the same Session to the same Worktree."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "eidos:agent-session:" + spawn_item_id))


def _base_ref(agent_id: str) -> str:
    return f"refs/eidos/agent-workspaces/{agent_id}/base"


def _delivery_ref(agent_id: str) -> str:
    return f"refs/eidos/agent-workspaces/{agent_id}/delivery"


class AgentWorkspaceService:
    def __init__(
        self,
        database: Database,
        manager: WorktreeManager,
        snapshots: WorktreeSnapshotService,
        *,
        git: GitCli | None = None,
    ) -> None:
        self.database = database
        self.manager = manager
        self.snapshots = snapshots
        self.git = git or GitCli()

    # Creation ------------------------------------------------------------

    def prepare(self, parent_session_id: str, spawn_item_id: str) -> PreparedAgentWorkspace:
        """Create, or resume creating, the Worktree for one spawn ToolCall."""
        operation_id = OPERATION_PREFIX + spawn_item_id
        session_id = child_session_id(spawn_item_id)
        lifecycle = self.manager.lifecycle
        try:
            operation = lifecycle.read(WorktreeLifecycleScope.SESSION_CREATE, operation_id)
            if operation is None:
                operation = self._prepare_operation(parent_session_id, operation_id, session_id)
            if operation.state is WorktreeLifecycleState.CLEANUP_REQUIRED:
                raise AgentWorkspaceError("agent_worktree_recovery_required")
            snapshot = self.snapshots.snapshots.read(operation.snapshot_id or "")
            if snapshot is None:
                raise AgentWorkspaceError("agent_worktree_failed")
            self.snapshots.verify(snapshot)
            worktree = self.manager.create_prepared(
                self.manager.prepared_from_lifecycle(operation), compensate_on_failure=True,
            )
            if operation.state is WorktreeLifecycleState.PREPARED:
                lifecycle.update_state(
                    WorktreeLifecycleScope.SESSION_CREATE, operation_id,
                    WorktreeLifecycleState.WORKTREE_CREATED,
                )
            changes = self.snapshots.read_changes(snapshot)
            self.manager.restore_snapshot_state(
                worktree.id, head=snapshot.head, changes=changes, expected_fingerprint=None,
            )
            root = Path(worktree.worktree_root)
            observed = self.manager.source_snapshot(root, include_local_changes=True)
            if observed.head != snapshot.head or observed.changes != changes:
                raise AgentWorkspaceError("agent_worktree_failed")
            tree = self.git.worktree_tree(root)
        except AgentWorkspaceError:
            raise
        except (WorktreeError, GitError, OSError, ValueError) as error:
            logger.warning("Agent Worktree creation failed operation_id=%s code=%s",
                           operation_id, getattr(error, "code", type(error).__name__))
            raise AgentWorkspaceError("agent_worktree_failed") from error
        return PreparedAgentWorkspace(operation_id, session_id, worktree.id, tree)

    def _prepare_operation(
        self, parent_session_id: str, operation_id: str, session_id: str,
    ) -> WorktreeLifecycleOperation:
        source = SessionRepository(self.database).read_session_projection(parent_session_id)
        if source is None or source.project is None or not source.project.git_available:
            raise AgentWorkspaceError("agent_worktree_requires_git")
        snapshot_id = "agent-" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:40]
        snapshot = self.snapshots.snapshots.read(snapshot_id)
        project = project_for_session(self.manager.project(source.project.id), source.session)
        if snapshot is None:
            if source.worktree is not None:
                snapshot = self.snapshots.save(
                    self.manager.read_worktree(source.worktree.worktree_id), snapshot_id,
                    replace_older=False,
                )
            else:
                snapshot = self.snapshots.save_local(
                    project, workspace_root=Path(source.session.workspace_root),
                    session_id=source.session.id, snapshot_id=snapshot_id,
                )
        self.snapshots.verify(snapshot)
        plan = self.manager.prepare_create(
            source.session.workspace_root, base_ref=snapshot.head, project=project,
        )
        now = datetime.now(UTC).replace(microsecond=0)
        return self.manager.lifecycle.prepare(WorktreeLifecycleOperation(
            scope=WorktreeLifecycleScope.SESSION_CREATE,
            operation_id=operation_id,
            state=WorktreeLifecycleState.PREPARED,
            project_id=plan.project_id,
            workspace_roots=source.session.roots,
            repository_root=plan.repository_root,
            worktree_id=plan.id,
            worktree_root=plan.worktree_root,
            base_ref=plan.base_ref,
            branch=plan.branch,
            base_commit=plan.base_commit,
            session_id=session_id,
            snapshot_id=snapshot.id,
            snapshot_head=snapshot.head,
            snapshot_fingerprint=snapshot.source_fingerprint,
            created_at=now,
            updated_at=now,
        ))

    def pin_base(self, prepared: PreparedAgentWorkspace, agent_id: str) -> None:
        self.git.pin_object(self._root(prepared.worktree_id), _base_ref(agent_id), prepared.base_tree)

    def complete(self, prepared: PreparedAgentWorkspace) -> None:
        """Close the lifecycle after the delegation that owns it committed."""
        lifecycle = self.manager.lifecycle
        operation = lifecycle.read(WorktreeLifecycleScope.SESSION_CREATE, prepared.operation_id)
        if operation is None or operation.state is WorktreeLifecycleState.COMPLETED:
            return
        if operation.state in {WorktreeLifecycleState.PREPARED, WorktreeLifecycleState.WORKTREE_CREATED}:
            lifecycle.update_state(WorktreeLifecycleScope.SESSION_CREATE, prepared.operation_id,
                                   WorktreeLifecycleState.SESSION_CREATED)
        lifecycle.update_state(WorktreeLifecycleScope.SESSION_CREATE, prepared.operation_id,
                               WorktreeLifecycleState.COMPLETED)
        self._drop_snapshot(operation.snapshot_id)
        self.manager.touch_last_used(prepared.worktree_id)

    def abandon(self, prepared: PreparedAgentWorkspace) -> None:
        """Drop the Worktree of a spawn whose delegation did not commit."""
        if self._owner(prepared.worktree_id) is not None:
            return
        try:
            self._remove_worktree(prepared.worktree_id)
            self._close_removed(prepared.operation_id)
        except Exception:
            logger.exception("Abandoned agent Worktree cleanup failed operation_id=%s", prepared.operation_id)

    def reconcile(self) -> None:
        """Finish or roll back spawn Worktrees left by an interrupted Runtime.

        Call only at startup, before any Run can spawn children.
        """
        lifecycle = self.manager.lifecycle
        for operation in lifecycle.list_unfinished():
            if (operation.scope is not WorktreeLifecycleScope.SESSION_CREATE
                    or not operation.operation_id.startswith(OPERATION_PREFIX)
                    or operation.worktree_id is None
                    or operation.state is WorktreeLifecycleState.CLEANUP_REQUIRED):
                continue
            try:
                if self._owner(operation.worktree_id) is not None:
                    self.complete(PreparedAgentWorkspace(operation.operation_id, operation.session_id or "",
                                                         operation.worktree_id, ""))
                    continue
                # The spawn ToolCall never committed its delegation. Its outcome
                # is recorded as unknown and is not replayed; drop the Worktree.
                self._remove_worktree(operation.worktree_id)
                self._close_removed(operation.operation_id)
            except Exception as error:
                logger.exception("Agent Worktree recovery requires cleanup operation_id=%s", operation.operation_id)
                lifecycle.update_state(WorktreeLifecycleScope.SESSION_CREATE, operation.operation_id,
                                       WorktreeLifecycleState.CLEANUP_REQUIRED,
                                       error_code=getattr(error, "code", None) or "agent_worktree_recovery_failed")
        for agent_id, worktree_id in self._orphaned():
            try:
                self._remove_worktree(worktree_id, agent_id=agent_id)
            except Exception:
                logger.exception("Orphaned agent Worktree cleanup failed worktree_id=%s", worktree_id)

    def _close_removed(self, operation_id: str) -> None:
        lifecycle = self.manager.lifecycle
        operation = lifecycle.read(WorktreeLifecycleScope.SESSION_CREATE, operation_id)
        if operation is None or operation.state is WorktreeLifecycleState.COMPLETED:
            return
        if operation.state is not WorktreeLifecycleState.WORKTREE_DELETED:
            lifecycle.update_state(WorktreeLifecycleScope.SESSION_CREATE, operation_id,
                                   WorktreeLifecycleState.WORKTREE_DELETED)
        lifecycle.update_state(WorktreeLifecycleScope.SESSION_CREATE, operation_id,
                               WorktreeLifecycleState.COMPLETED)
        self._drop_snapshot(operation.snapshot_id)

    def _drop_snapshot(self, snapshot_id: str | None) -> None:
        """The creation snapshot is only needed until the Worktree exists.

        It references the parent Session, which could not be deleted while
        the snapshot row remained.
        """
        snapshot = self.snapshots.snapshots.read(snapshot_id) if snapshot_id else None
        if snapshot is None:
            return
        try:
            self.snapshots.delete_anchor_if_expected(snapshot)
            self.snapshots.artifacts.delete(snapshot.artifact_path)
            self.snapshots.snapshots.delete(snapshot.id)
        except Exception:
            logger.exception("Agent Worktree creation snapshot cleanup deferred snapshot_id=%s", snapshot.id)

    # Deliveries ------------------------------------------------------------

    def capture(self, parent_run_id: str, agent_id: str) -> Delivery:
        """Pin the child's current files and list their changes from the base."""
        delegation = self._delegation(parent_run_id, agent_id)
        if self._child_active(delegation):
            raise AgentWorkspaceError("agent_still_running")
        root = self._root(delegation["worktree_id"])
        try:
            fingerprint = self.manager.source_snapshot(root, include_local_changes=True).fingerprint
            latest = self._latest(agent_id)
            if latest is not None and latest.status != "discarded" and self._fingerprint(latest.id) == fingerprint:
                return latest
            result_tree = self.git.worktree_tree(root)
            changes = self.git.tree_changes(root, delegation["base_tree"], result_tree)
        except (WorktreeError, GitError, OSError, ValueError) as error:
            code = "delivery_submodule_unsupported" if getattr(error, "code", "") == "worktree_gitlink_unsupported" else "delivery_failed"
            raise AgentWorkspaceError(code) from error
        entries = []
        total = 0
        for change in changes:
            if change.old_mode == 0o160000 or change.new_mode == 0o160000:
                raise AgentWorkspaceError("delivery_submodule_unsupported")
            kind: Literal["added", "modified", "deleted"] = (
                "added" if change.status == "A" else "deleted" if change.status == "D" else "modified")
            entries.append(DeliveryEntry(path=change.path, kind=kind, base=change.old_object,
                result=change.new_object, mode=change.new_mode or change.old_mode, binary=change.binary,
                additions=change.additions, deletions=change.deletions))
        if len(entries) > MAX_DELIVERY_FILES:
            raise AgentWorkspaceError("delivery_too_large")
        for entry in entries:
            if entry.result is not None:
                size = self._object_size(root, entry.result)
                if size > MAX_FILE_BYTES:
                    raise AgentWorkspaceError("delivery_too_large")
                total += size
        if total > MAX_DELIVERY_BYTES:
            raise AgentWorkspaceError("delivery_too_large")
        self.git.pin_object(root, _delivery_ref(agent_id), result_tree)
        delivery_id = str(uuid.uuid4())
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE workspace_deliveries SET status='stale' WHERE owner_kind='agent' AND owner_id=? AND status='ready'",
                (agent_id,))
            connection.execute(
                "INSERT INTO workspace_deliveries (id,owner_kind,owner_id,worktree_id,base_tree,result_tree,fingerprint,entries_json,status,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (delivery_id, "agent", agent_id, delegation["worktree_id"], delegation["base_tree"], result_tree,
                 fingerprint, json.dumps([entry.model_dump(mode="json") for entry in entries]), "ready", now_ms()))
        return Delivery(id=delivery_id, agent_id=agent_id, worktree_id=delegation["worktree_id"],
                        base_tree=delegation["base_tree"], result_tree=result_tree, entries=tuple(entries), status="ready")

    def diff_page(self, delivery: Delivery, *, path: str | None, offset: int, max_bytes: int) -> tuple[str, int, bool]:
        root = self._root(delivery.worktree_id)
        try:
            raw, truncated = self.git.tree_diff(root, delivery.base_tree, delivery.result_tree,
                                                output_limit_bytes=offset + max_bytes + 4, path=path)
        except (GitError, OSError, ValueError) as error:
            raise AgentWorkspaceError("delivery_failed") from error
        body = raw[offset:offset + max_bytes]
        text = body.decode("utf-8", errors="ignore")
        next_offset = offset + len(text.encode("utf-8"))
        return text, next_offset, truncated or next_offset < len(raw)

    def read_object(self, delivery: Delivery, object_id: str) -> bytes:
        try:
            return self.git.read_blob(self._root(delivery.worktree_id), object_id,
                                      output_limit_bytes=MAX_FILE_BYTES + 1)
        except (GitError, OSError, ValueError) as error:
            raise AgentWorkspaceError("delivery_failed") from error

    def merge_text(self, delivery: Delivery, current: bytes, base: bytes, other: bytes) -> bytes | None:
        """Return the clean three-way merge, or None when Git reports conflicts."""
        try:
            merged, conflicts = self.git.merge_file(self._root(delivery.worktree_id), current, base, other)
        except (GitError, OSError, ValueError) as error:
            raise AgentWorkspaceError("delivery_failed") from error
        return None if conflicts else merged

    def mark_integration(self, delivery_id: str, *, complete: bool) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE workspace_deliveries SET status=?,integrated_at=? WHERE id=? AND status IN ('ready','partially_integrated')",
                ("integrated" if complete else "partially_integrated", now_ms(), delivery_id))

    def latest_delivery(self, agent_id: str) -> Delivery | None:
        return self._latest(agent_id)

    # Removal -------------------------------------------------------------

    def discard(self, parent_run_id: str, agent_id: str) -> None:
        delegation = self._delegation(parent_run_id, agent_id)
        if self._child_active(delegation):
            raise AgentWorkspaceError("agent_still_running")
        self._remove_worktree(delegation["worktree_id"], agent_id=agent_id)

    def release_for_session(self, session_id: str) -> None:
        """Remove every child Worktree before its parent Session's facts go."""
        with self.database.lock:
            rows = self.database.connection().execute(
                "SELECT d.id,d.worktree_id FROM agent_delegations d JOIN runs p ON p.id=d.parent_run_id "
                "WHERE p.session_id=? AND d.worktree_id IS NOT NULL", (session_id,)).fetchall()
        for row in rows:
            self._remove_worktree(row["worktree_id"], agent_id=row["id"])
        with self.database.lock:
            snapshot_ids = [row[0] for row in self.database.connection().execute(
                "SELECT id FROM worktree_snapshots WHERE session_id=? AND id LIKE 'agent-%'", (session_id,))]
        for snapshot_id in snapshot_ids:
            self._drop_snapshot(snapshot_id)

    def _remove_worktree(self, worktree_id: str, *, agent_id: str | None = None) -> None:
        try:
            worktree = self.manager.repository.read_worktree(worktree_id)
        except Exception:
            worktree = None
        if worktree is not None and worktree.state is not WorktreeState.DELETED:
            root = Path(worktree.worktree_root)
            if agent_id is not None and root.exists():
                for ref in (_base_ref(agent_id), _delivery_ref(agent_id)):
                    try:
                        self.git.unpin_ref(root, ref)
                    except (GitError, OSError, ValueError):
                        pass
            # The owner chose to drop this workspace, or its spawn never
            # committed. Clean and remove it like an Eidos-owned retention target.
            self.manager.clean_for_retention(worktree_id)
        if agent_id is not None:
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE workspace_deliveries SET status='discarded' WHERE owner_kind='agent' AND owner_id=? "
                    "AND status IN ('ready','stale','partially_integrated')", (agent_id,))

    # Helpers -------------------------------------------------------------

    def _root(self, worktree_id: str) -> Path:
        worktree = self.manager.repository.read_worktree(worktree_id)
        if worktree is None or worktree.state is WorktreeState.DELETED:
            raise AgentWorkspaceError("agent_workspace_discarded")
        return Path(worktree.worktree_root)

    def _delegation(self, parent_run_id: str, agent_id: str) -> sqlite3.Row:
        with self.database.lock:
            row = self.database.connection().execute(
                "SELECT * FROM agent_delegations WHERE parent_run_id=? AND id=?", (parent_run_id, agent_id)).fetchone()
        if row is None:
            raise AgentWorkspaceError("agent_not_owned_by_run")
        if row["workspace_mode"] != "worktree" or row["worktree_id"] is None:
            raise AgentWorkspaceError("agent_workspace_shared")
        return row

    def _child_active(self, delegation: sqlite3.Row) -> bool:
        from eidos_runtime.domain.collaboration import ACTIVE_STATUSES
        with self.database.lock:
            row = self.database.connection().execute(
                "SELECT status FROM runs WHERE id=?", (delegation["child_run_id"],)).fetchone()
        return row is not None and row["status"] in ACTIVE_STATUSES

    def _owner(self, worktree_id: str) -> str | None:
        with self.database.lock:
            row = self.database.connection().execute(
                "SELECT id FROM agent_delegations WHERE worktree_id=?", (worktree_id,)).fetchone()
        return row["id"] if row else None

    def _orphaned(self) -> tuple[tuple[str, str], ...]:
        with self.database.lock:
            rows = self.database.connection().execute(
                "SELECT d.id,d.worktree_id FROM agent_delegations d JOIN worktrees w ON w.id=d.worktree_id "
                "WHERE w.state != 'deleted' AND NOT EXISTS (SELECT 1 FROM sessions s WHERE s.id=d.child_session_id)"
            ).fetchall()
        return tuple((row["id"], row["worktree_id"]) for row in rows)

    def _latest(self, agent_id: str) -> Delivery | None:
        with self.database.lock:
            row = self.database.connection().execute(
                "SELECT * FROM workspace_deliveries WHERE owner_kind='agent' AND owner_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (agent_id,)).fetchone()
        if row is None:
            return None
        return Delivery(id=row["id"], agent_id=agent_id, worktree_id=row["worktree_id"], base_tree=row["base_tree"],
                        result_tree=row["result_tree"], status=row["status"],
                        entries=tuple(DeliveryEntry.model_validate(value) for value in json.loads(row["entries_json"])))

    def _fingerprint(self, delivery_id: str) -> str | None:
        with self.database.lock:
            row = self.database.connection().execute(
                "SELECT fingerprint FROM workspace_deliveries WHERE id=?", (delivery_id,)).fetchone()
        return row["fingerprint"] if row else None

    def _object_size(self, root: Path, object_id: str) -> int:
        return self.git.object_size(root, object_id)
