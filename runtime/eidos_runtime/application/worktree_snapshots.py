from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import logging
from pathlib import Path
from collections.abc import Callable

from eidos_runtime.domain.project import Project
from eidos_runtime.domain.worktree import BranchOwnership, Worktree
from eidos_runtime.domain.worktree_snapshot import WorktreeSnapshot
from eidos_runtime.git.errors import WorktreeError
from eidos_runtime.git.manager import WorktreeManager
from eidos_runtime.git.models import GitSourceSnapshot, GitWorkingTreePatch
from eidos_runtime.git.snapshot_artifacts import SnapshotArtifactStore
from eidos_runtime.persistence.worktree_snapshots import WorktreeSnapshotRepository


class WorktreeSnapshotService:
    """Own snapshot artifacts, hidden refs, metadata, and reconciliation."""

    def __init__(
        self,
        manager: WorktreeManager,
        snapshots: WorktreeSnapshotRepository,
        artifacts: SnapshotArtifactStore,
        *,
        session_for_worktree: Callable[[str], str | None],
        logger: logging.Logger,
    ) -> None:
        self.manager = manager
        self.snapshots = snapshots
        self.artifacts = artifacts
        self.session_for_worktree = session_for_worktree
        self.logger = logger

    def save(
        self,
        worktree: Worktree,
        snapshot_id: str,
        *,
        replace_older: bool = True,
    ) -> WorktreeSnapshot:
        root = Path(worktree.worktree_root)
        source = self.manager.source_snapshot(root, include_local_changes=True)
        source_after = self.manager.source_snapshot(root, include_local_changes=True)
        if not _same_snapshot(source, source_after):
            raise WorktreeError("worktree_source_changed")
        if source.changes is None:
            raise WorktreeError("worktree_snapshot_required")
        artifact = self.artifacts.write(snapshot_id, source.changes)
        project = self.manager.project(worktree.project_id)
        self.manager.git.create_snapshot_anchor(
            Path(project.workspace_root), snapshot_id, source.head
        )
        now = _now()
        existing = self.snapshots.list_for_worktree(worktree.id)
        if existing:
            latest_created_at = max(snapshot.created_at for snapshot in existing)
            if now <= latest_created_at:
                now = latest_created_at + timedelta(milliseconds=1)
        snapshot = WorktreeSnapshot(
            id=snapshot_id,
            worktree_id=worktree.id,
            workspace_root=str(root.resolve()),
            session_id=self.session_for_worktree(worktree.id),
            project_id=worktree.project_id,
            base_ref=worktree.base_ref,
            base_commit=worktree.base_commit,
            head=source.head,
            branch=source.branch,
            checkout_branch=worktree.checkout_branch,
            branch_ownership=worktree.branch_ownership,
            dirty=source.status.dirty,
            staged_paths=source.status.staged_paths,
            unstaged_paths=source.status.unstaged_paths,
            untracked_paths=source.status.untracked_paths,
            conflict_paths=source.status.conflict_paths,
            source_fingerprint=source.fingerprint,
            artifact_path=str(artifact.path),
            artifact_sha256=artifact.artifact_sha256,
            full_patch_sha256=artifact.full_patch_sha256,
            staged_patch_sha256=artifact.staged_patch_sha256,
            format_version=artifact.format_version,
            created_at=now,
            updated_at=now,
        )
        saved = self.snapshots.insert(snapshot)
        for older in self.snapshots.list_for_worktree(worktree.id) if replace_older else ():
            if older.id == saved.id or older.state.value != "ready":
                continue
            if self.snapshots.referenced_by_checkpoint(older.id):
                continue
            try:
                self.delete_anchor_if_expected(older)
                self.artifacts.delete(older.artifact_path)
                self.snapshots.delete(older.id)
            except Exception:
                self.logger.warning(
                    "older Worktree snapshot cleanup deferred",
                    extra={"snapshot_id": older.id, "worktree_id": worktree.id},
                )
        return saved

    def save_local(
        self,
        project: Project,
        *,
        workspace_root: Path,
        session_id: str,
        snapshot_id: str,
    ) -> WorktreeSnapshot:
        source = self.manager.source_snapshot(
            workspace_root, include_local_changes=True
        )
        source_after = self.manager.source_snapshot(
            workspace_root, include_local_changes=True
        )
        if not _same_snapshot(source, source_after):
            raise WorktreeError("worktree_source_changed")
        if source.changes is None:
            raise WorktreeError("worktree_snapshot_required")
        if (
            project.git_common_dir is None
            or source.discovery.git_common_dir != project.git_common_dir
        ):
            raise WorktreeError("workspace_identity_changed")
        artifact = self.artifacts.write(snapshot_id, source.changes)
        self.manager.git.create_snapshot_anchor(
            Path(project.workspace_root), snapshot_id, source.head
        )
        now = _now()
        snapshot = WorktreeSnapshot(
            id=snapshot_id,
            worktree_id=None,
            workspace_root=str(workspace_root.resolve()),
            session_id=session_id,
            project_id=project.id,
            base_ref=source.head,
            base_commit=source.head,
            head=source.head,
            branch=source.branch,
            checkout_branch=source.branch,
            branch_ownership=(
                BranchOwnership.USER
                if source.branch is not None
                else BranchOwnership.NONE
            ),
            dirty=source.status.dirty,
            staged_paths=source.status.staged_paths,
            unstaged_paths=source.status.unstaged_paths,
            untracked_paths=source.status.untracked_paths,
            conflict_paths=source.status.conflict_paths,
            source_fingerprint=source.fingerprint,
            artifact_path=str(artifact.path),
            artifact_sha256=artifact.artifact_sha256,
            full_patch_sha256=artifact.full_patch_sha256,
            staged_patch_sha256=artifact.staged_patch_sha256,
            format_version=artifact.format_version,
            created_at=now,
            updated_at=now,
        )
        return self.snapshots.insert(snapshot)

    def verify(self, snapshot: WorktreeSnapshot) -> None:
        actual = self.read_anchor(snapshot)
        if actual != snapshot.head:
            raise WorktreeError("worktree_snapshot_anchor_mismatch")
        try:
            self.artifacts.verify(snapshot.artifact_path, snapshot.artifact_sha256)
            changes = self.artifacts.read(snapshot.artifact_path)
        except (OSError, ValueError) as error:
            raise WorktreeError("worktree_snapshot_checksum_mismatch") from error
        if (
            _sha256_bytes(changes.full_patch) != snapshot.full_patch_sha256
            or _sha256_bytes(changes.staged_patch) != snapshot.staged_patch_sha256
        ):
            raise WorktreeError("worktree_snapshot_checksum_mismatch")

    def verify_artifact(self, snapshot: WorktreeSnapshot) -> None:
        try:
            changes = self.artifacts.read(snapshot.artifact_path)
        except (OSError, ValueError) as error:
            raise WorktreeError("worktree_snapshot_checksum_mismatch") from error
        if (
            _sha256_bytes(changes.full_patch) != snapshot.full_patch_sha256
            or _sha256_bytes(changes.staged_patch) != snapshot.staged_patch_sha256
        ):
            raise WorktreeError("worktree_snapshot_checksum_mismatch")

    def read_changes(self, snapshot: WorktreeSnapshot) -> GitWorkingTreePatch:
        try:
            return self.artifacts.read(snapshot.artifact_path)
        except (OSError, ValueError) as error:
            raise WorktreeError("worktree_snapshot_checksum_mismatch") from error

    def read_anchor(self, snapshot: WorktreeSnapshot) -> str | None:
        project = self.manager.project(snapshot.project_id)
        try:
            return self.manager.git.snapshot_anchor(
                Path(project.workspace_root), snapshot.id
            )
        except Exception as error:
            raise WorktreeError("worktree_snapshot_anchor_unavailable") from error

    def delete_anchor_if_expected(self, snapshot: WorktreeSnapshot) -> None:
        project = self.manager.project(snapshot.project_id)
        root = Path(project.workspace_root)
        try:
            actual = self.manager.git.snapshot_anchor(root, snapshot.id)
        except Exception as error:
            raise WorktreeError("worktree_snapshot_anchor_unavailable") from error
        if actual is None:
            return
        if actual != snapshot.head:
            raise WorktreeError("worktree_snapshot_anchor_changed")
        try:
            deleted = self.manager.git.delete_snapshot_anchor_if_equals(
                root, snapshot.id, snapshot.head
            )
        except Exception as error:
            raise WorktreeError("worktree_snapshot_anchor_unavailable") from error
        if not deleted:
            raise WorktreeError("worktree_snapshot_anchor_changed")

    def delete_for_worktree(self, worktree_id: str) -> None:
        for snapshot in self.snapshots.list_for_worktree(worktree_id):
            if self.snapshots.referenced_by_checkpoint(snapshot.id):
                continue
            self.delete_anchor_if_expected(snapshot)
            try:
                self.artifacts.delete(snapshot.artifact_path)
            except (OSError, ValueError) as error:
                raise WorktreeError("worktree_snapshot_cleanup_required") from error
            self.snapshots.delete(snapshot.id)

    def has_ready(self, worktree_id: str) -> bool:
        return self.snapshots.latest_ready(worktree_id) is not None

    def latest_ready_id(self, worktree_id: str) -> str | None:
        snapshot = self.snapshots.latest_ready(worktree_id)
        return snapshot.id if snapshot is not None else None

    def latest_ready(self, worktree_id: str) -> WorktreeSnapshot | None:
        return self.snapshots.latest_ready(worktree_id)

    def reconcile_storage(self) -> None:
        all_snapshots = self.snapshots.list_all()
        rows = {
            snapshot.id: snapshot
            for snapshot in all_snapshots
            if snapshot.state.value == "ready"
        }
        for snapshot in rows.values():
            try:
                self.verify(snapshot)
                if self.read_anchor(snapshot) != snapshot.head:
                    raise ValueError("snapshot anchor is missing or changed")
            except (OSError, ValueError, WorktreeError):
                self.snapshots.mark_invalid(snapshot.id)
        known_snapshot_ids = {snapshot.id for snapshot in all_snapshots}
        known_snapshot_ids.update(
            operation.snapshot_id
            for operation in self.manager.lifecycle.list_unfinished()
            if operation.snapshot_id is not None
        )
        for project in self.manager.repository.list_projects():
            if project.git_repository_root is None:
                continue
            try:
                anchors = self.manager.git.list_snapshot_anchors(
                    Path(project.workspace_root)
                )
            except Exception:
                self.logger.warning(
                    "snapshot anchor reconciliation skipped for project",
                    extra={"project_id": project.id},
                )
                continue
            for snapshot_id, _head in anchors:
                if snapshot_id not in known_snapshot_ids:
                    self.logger.warning(
                        "orphan snapshot anchor candidate retained",
                        extra={"project_id": project.id, "snapshot_id": snapshot_id},
                    )
        known_paths = {Path(snapshot.artifact_path).resolve() for snapshot in all_snapshots}
        for snapshot in all_snapshots:
            if snapshot.state.value != "restored":
                continue
            try:
                self.delete_anchor_if_expected(snapshot)
            except (OSError, ValueError, WorktreeError):
                self.logger.warning(
                    "restored Worktree snapshot anchor cleanup deferred",
                    extra={"snapshot_id": snapshot.id},
                )
                continue
            try:
                self.artifacts.delete(snapshot.artifact_path)
            except (OSError, ValueError):
                self.logger.warning(
                    "restored Worktree snapshot artifact cleanup deferred",
                    extra={"snapshot_id": snapshot.id},
                )
        for path in self.artifacts.list_directories():
            if path.resolve() not in known_paths:
                self.logger.warning(
                    "orphan snapshot artifact candidate retained",
                    extra={"artifact_path": str(path)},
                )


def _now() -> datetime:
    current = datetime.now(UTC)
    return current.replace(microsecond=(current.microsecond // 1000) * 1000)


def _same_snapshot(first: GitSourceSnapshot, second: GitSourceSnapshot) -> bool:
    return (
        first.head == second.head
        and first.branch == second.branch
        and first.status == second.status
        and first.fingerprint == second.fingerprint
        and first.changes == second.changes
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


__all__ = ["WorktreeSnapshotService"]
