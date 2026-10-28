from __future__ import annotations

import base64
from pathlib import Path
import subprocess
from uuid import uuid4

import pytest

from eidos_runtime.application.checkpoints import CheckpointApplication
from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.projects import ProjectApplication
from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.application.worktree_retention import WorktreeRetentionService
from eidos_runtime.application.workspace import WorkspaceExplorerApplication
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.git import WorktreeManager
from eidos_runtime.protocol.methods import (
    CheckpointCreateRequestDto,
    CheckpointListRequestDto,
    SessionCreateRequestDto,
    SessionGitStatusRequestDto,
    WorkspaceReadAssetRequestDto,
    WorkspaceReadFilePreviewRequestDto,
)
from eidos_runtime.protocol.project_workspaces import (
    ProjectCreateFoldersRequest,
    ProjectRootInput,
    ProjectUpdateRequest,
)


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _repository(path: Path, marker: str) -> Path:
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.name", "Eidos Tests")
    _git(path, "config", "user.email", "eidos-tests@example.com")
    (path / "repository-marker.txt").write_text(marker, encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "initial")
    return path


def _runtime(
    tmp_path: Path,
) -> tuple[
    SessionStore,
    WorktreeManager,
    ProjectApplication,
    SessionApplication,
]:
    store = SessionStore(tmp_path / "data")
    store.initialize()
    manager = WorktreeManager(
        store.database,
        managed_root=tmp_path / "managed-worktrees",
    )
    projects = ProjectApplication(
        manager.repository,
        create_project=manager.create_project,
    )
    sessions = SessionApplication(
        store,
        scan_text=lambda value: value,
        worktree_manager=manager,
    )
    return store, manager, projects, sessions


def _create_project(
    projects: ProjectApplication,
    roots: list[tuple[str, Path]],
    primary_root_id: str,
):
    return projects.create_folders(
        ProjectCreateFoldersRequest(
            operationId=str(uuid4()),
            name="Multi-root project",
            roots=[
                ProjectRootInput(id=root_id, path=str(path))
                for root_id, path in roots
            ],
            primaryRootId=primary_root_id,
        )
    )


def test_local_session_and_run_capture_all_configured_roots(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    primary.mkdir()
    secondary.mkdir()
    store, _manager, projects, sessions = _runtime(tmp_path)

    try:
        project = _create_project(
            projects,
            [("root-primary", primary), ("root-secondary", secondary)],
            "root-primary",
        )
        session = sessions.create(
            SessionCreateRequestDto(projectId=project.id)
        ).root
        run, _item = store.create_run(session["id"], "inspect both roots")

        session_roots = {root["id"]: root for root in session["roots"]}
        identity = store.workspace_for_run(run["id"])
        snapshot = store.read_run_resolution_snapshot(run["id"])
        snapshot_identity = snapshot.workspace_identity

        assert session_roots["root-primary"]["path"] == str(primary.resolve())
        assert session_roots["root-secondary"]["path"] == str(secondary.resolve())
        assert identity.path == primary.resolve()
        assert identity.root_id == "root-primary"
        assert len(identity.additional_roots) == 1
        assert identity.additional_roots[0].root_id == "root-secondary"
        assert identity.additional_roots[0].path == secondary.resolve()
        assert snapshot_identity.root_id == "root-primary"
        assert snapshot_identity.path == str(primary.resolve())
        assert snapshot_identity.additional_roots is not None
        assert snapshot_identity.additional_roots[0].root_id == "root-secondary"
        assert snapshot_identity.additional_roots[0].path == str(secondary.resolve())
    finally:
        store.close()


def test_secondary_root_file_reads_keep_version_checks_and_path_boundaries(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    primary.mkdir()
    secondary.mkdir()
    source = secondary / "shared.txt"
    source.write_text("initial shared content\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside secret\n", encoding="utf-8")
    store, manager, projects, sessions = _runtime(tmp_path)
    explorer = WorkspaceExplorerApplication(
        store.typed_runtime_repository(),
        worktree_manager=manager,
        scan_text=lambda value: value,
    )

    try:
        project = _create_project(
            projects,
            [("root-primary", primary), ("root-secondary", secondary)],
            "root-primary",
        )
        session = sessions.create(
            SessionCreateRequestDto(projectId=project.id)
        ).root
        session_id = session["id"]
        secondary_root = str(secondary.resolve())
        preview = explorer.read_file_preview(
            WorkspaceReadFilePreviewRequestDto(
                sessionId=session_id,
                workspaceRoot=secondary_root,
                path="shared.txt",
            )
        ).root
        assert preview["content"] == "initial shared content\n"

        asset_request = WorkspaceReadAssetRequestDto(
            sessionId=session_id,
            workspaceRoot=secondary_root,
            path="shared.txt",
            executionRoot=secondary_root,
            version=preview["version"],
        )
        asset = explorer.read_asset(asset_request).root
        assert base64.b64decode(asset["data"]) == b"initial shared content\n"

        source.write_text("updated shared content\n", encoding="utf-8")
        with pytest.raises(ApplicationError) as changed:
            explorer.read_asset(asset_request)
        assert changed.value.code == "WORKSPACE_FILE_CHANGED"

        with pytest.raises(ApplicationError) as escaped:
            explorer.read_file_preview(
                WorkspaceReadFilePreviewRequestDto(
                    sessionId=session_id,
                    workspaceRoot=secondary_root,
                    path="../outside.txt",
                )
            )
        assert escaped.value.code == "WORKSPACE_BOUNDARY_VIOLATION"
    finally:
        explorer.close()
        store.close()


def test_root_id_selects_a_secondary_git_repository_for_status(
    tmp_path: Path,
) -> None:
    primary = _repository(tmp_path / "primary", "primary repository\n")
    secondary = _repository(tmp_path / "secondary", "secondary repository\n")
    store, _manager, projects, sessions = _runtime(tmp_path)

    try:
        project = _create_project(
            projects,
            [("root-primary", primary), ("root-secondary", secondary)],
            "root-primary",
        )
        session = sessions.create(
            SessionCreateRequestDto(projectId=project.id)
        ).root
        (secondary / "secondary-only.txt").write_text(
            "secondary change\n",
            encoding="utf-8",
        )

        secondary_request = SessionGitStatusRequestDto(
            sessionId=session["id"],
            rootId="root-secondary",
        )
        primary_request = SessionGitStatusRequestDto(
            sessionId=session["id"],
            rootId="root-primary",
        )
        secondary_status = sessions.for_git_request(secondary_request).git_status(
            secondary_request
        ).root
        primary_status = sessions.for_git_request(primary_request).git_status(
            primary_request
        ).root

        assert "secondary-only.txt" in secondary_status["untrackedFiles"]
        assert "secondary-only.txt" not in primary_status["untrackedFiles"]
    finally:
        store.close()


def test_primary_switch_keeps_existing_worktree_and_checkpoint_on_original_repo(
    tmp_path: Path,
) -> None:
    primary = _repository(tmp_path / "primary", "original primary\n")
    secondary = _repository(tmp_path / "secondary", "future primary\n")
    store, manager, projects, sessions = _runtime(tmp_path)
    retention = WorktreeRetentionService(store.database, manager)
    checkpoints = CheckpointApplication(
        store,
        store.checkpoint_repository(),
        worktree_manager=manager,
        retention=retention,
    )

    try:
        project = _create_project(
            projects,
            [("root-primary", primary), ("root-secondary", secondary)],
            "root-primary",
        )
        session = sessions.create(
            SessionCreateRequestDto(
                projectId=project.id,
                executionMode="worktree",
            )
        ).root
        session_id = session["id"]
        worktree_id = session["worktree"]["worktreeId"]
        worktree_root = Path(session["worktree"]["worktreeRoot"])
        run, _item = store.enqueue_run(session_id, "checkpoint original repository")
        checkpoint = checkpoints.create(
            CheckpointCreateRequestDto(
                runId=run["id"],
                operationId=str(uuid4()),
            )
        ).checkpoint
        assert checkpoint.git_snapshot_id is not None
        snapshot = checkpoints.snapshot_service.snapshots.read(
            checkpoint.git_snapshot_id
        )
        assert snapshot is not None
        assert snapshot.repository_root == str(primary.resolve())

        update = projects.update(
            ProjectUpdateRequest(
                projectId=project.id,
                operationId=str(uuid4()),
                expectedRevision=1,
                name=project.name,
                roots=[
                    ProjectRootInput(id="root-primary", path=str(primary)),
                    ProjectRootInput(id="root-secondary", path=str(secondary)),
                ],
                primaryRootId="root-secondary",
            )
        )
        stored_session = store.typed_runtime_repository().read_session(session_id)
        stored_worktree = manager.read_worktree(worktree_id)
        run_identity = store.workspace_for_run(run["id"])

        assert update.workspace_root == str(secondary.resolve())
        assert update.primary_root_id == "root-secondary"
        assert stored_session is not None
        assert stored_session.workspace_root == str(primary.resolve())
        assert stored_session.worktree_id == worktree_id
        assert {root.id: root.path for root in stored_session.roots} == {
            "root-primary": str(primary.resolve()),
            "root-secondary": str(secondary.resolve()),
        }
        assert stored_worktree.repository_root == str(primary.resolve())
        assert run_identity.path == worktree_root.resolve()
        assert len(run_identity.additional_roots) == 1
        assert run_identity.additional_roots[0].root_id == "root-secondary"
        assert run_identity.additional_roots[0].path == secondary.resolve()
        checkpoints.snapshot_service.verify(snapshot)
        assert checkpoints.list(
            CheckpointListRequestDto(runId=run["id"])
        ).checkpoints[0].git_snapshot_id == checkpoint.git_snapshot_id
    finally:
        store.close()
