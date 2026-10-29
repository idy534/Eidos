from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess
import sqlite3
import shutil
from uuid import uuid4

import pytest

from eidos_runtime.application.checkpoints import CheckpointApplication
from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.projects import ProjectApplication
from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.application.worktree_retention import WorktreeRetentionService
from eidos_runtime.application.workspace import WorkspaceExplorerApplication
from eidos_runtime.db.storage import SessionStore, WorkspaceIdentity
from eidos_runtime.db.errors import WorkspaceIdentityChangedError
from eidos_runtime.db.database import DATABASE_NAME
from eidos_runtime.git import WorktreeManager
from eidos_runtime.protocol.methods import (
    CheckpointCreateRequestDto,
    CheckpointListRequestDto,
    SessionCreateRequestDto,
    SessionGitStatusRequestDto,
    WorkspaceListDirectoryRequestDto,
    WorkspaceReadAssetRequestDto,
    WorkspaceReadFilePreviewRequestDto,
)
from eidos_runtime.protocol.project_workspaces import (
    ProjectCreateFoldersRequest,
    ProjectReadRequest,
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


@pytest.mark.parametrize("target_root", ["primary", "secondary"])
def test_run_snapshot_preserves_git_metadata_identity_for_each_root(
    tmp_path: Path, target_root: str,
) -> None:
    from eidos_runtime.runtime.shell_orchestration import (
        GitWriteAccessError,
        resolve_git_write_roots,
    )

    primary = _repository(tmp_path / "primary", "primary\n")
    secondary = _repository(tmp_path / "secondary", "secondary\n")
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
        run, _item = store.create_run(session["id"], "check frozen Git identity")
        workspace = store.workspace_for_run(run["id"])
        snapshot = store.read_run_resolution_snapshot(run["id"])
        target_path = primary if target_root == "primary" else secondary
        identity = (
            workspace
            if target_root == "primary"
            else workspace.additional_roots[0]
        )
        snapshot_identity = snapshot.workspace_identity
        saved_identity = (
            snapshot_identity
            if target_root == "primary"
            else snapshot_identity.additional_roots[0]
        )
        assert identity.git_dir_identity is not None
        assert identity.git_common_identity is not None
        assert saved_identity.git_dir_identity == identity.git_dir_identity
        assert saved_identity.git_common_identity == identity.git_common_identity

        (target_path / ".git").rename(target_path / ".git-old")
        subprocess.run(["git", "init", "-q", str(target_path)], check=True)

        with pytest.raises(GitWriteAccessError, match="git_repository_identity_changed"):
            resolve_git_write_roots(identity)
    finally:
        store.close()


def test_secondary_root_file_reads_keep_version_checks_and_path_boundaries(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    primary.mkdir()
    secondary.mkdir()
    source = secondary / "shared.html"
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
                path="shared.html",
            )
        ).root
        assert preview["content"] == "initial shared content\n"

        asset_request = WorkspaceReadAssetRequestDto(
            sessionId=session_id,
            workspaceRoot=secondary_root,
            path="shared.html",
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


def test_secondary_git_status_rejects_replaced_frozen_git_metadata(
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
        request = SessionGitStatusRequestDto(
            sessionId=session["id"], rootId="root-secondary"
        )
        original_head = _git(secondary, "rev-parse", "HEAD")
        (secondary / ".git").rename(tmp_path / "displaced-secondary-git")
        _git(secondary, "init", "-q", "-b", "replacement")
        _git(secondary, "config", "user.name", "Eidos Tests")
        _git(secondary, "config", "user.email", "eidos-tests@example.com")
        (secondary / "replacement.txt").write_text("replacement repository\n")
        _git(secondary, "add", ".")
        _git(secondary, "commit", "-qm", "replacement")
        assert _git(secondary, "rev-parse", "HEAD") != original_head

        with pytest.raises(ApplicationError) as error:
            sessions.for_git_request(request).git_status(request)

        assert error.value.code == "WORKSPACE_IDENTITY_CHANGED"
    finally:
        store.close()


def test_worktree_git_status_rejects_replaced_source_common_directory(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path / "repository", "original repository\n")
    store, manager, projects, sessions = _runtime(tmp_path)
    try:
        project = _create_project(projects, [("root-primary", repository)], "root-primary")
        session = sessions.create(
            SessionCreateRequestDto(
                projectId=project.id,
                executionMode="worktree",
            )
        ).root
        request = SessionGitStatusRequestDto(
            sessionId=session["id"],
            rootId="root-primary",
        )
        worktree_root = Path(session["worktree"]["worktreeRoot"])
        worktree_metadata = worktree_root.stat()
        common_dir = (repository / ".git").resolve()
        manager.execution_identity = lambda _worktree_id: WorkspaceIdentity(
            path=worktree_root,
            device=worktree_metadata.st_dev,
            inode=worktree_metadata.st_ino,
            owner=worktree_metadata.st_uid,
            git_common_dir=common_dir,
        )
        (repository / ".git").rename(tmp_path / "displaced-common-dir")
        subprocess.run(["git", "init", "-q", str(repository)], check=True)

        with pytest.raises(ApplicationError) as error:
            sessions.for_git_request(request).git_status(request)

        assert error.value.code == "WORKSPACE_IDENTITY_CHANGED"
    finally:
        store.close()


def test_v45_project_session_migration_pins_git_metadata_before_replacement(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path / "repository", "original repository\n")
    data_directory = tmp_path / "data"
    store, _manager, projects, sessions = _runtime(tmp_path)
    try:
        project = _create_project(
            projects,
            [("root-primary", repository)],
            "root-primary",
        )
        session = sessions.create(
            SessionCreateRequestDto(projectId=project.id)
        ).root
        session_id = session["id"]
        connection = store.connection
        assert connection is not None
        roots = json.loads(connection.execute(
            "SELECT workspace_roots_json FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()[0])
        roots[0]["git_dir"] = None
        roots[0]["git_dir_identity"] = None
        roots[0]["git_common_identity"] = None
        connection.execute(
            "UPDATE sessions SET workspace_roots_json = ? WHERE id = ?",
            (json.dumps(roots), session_id),
        )
    finally:
        store.close()

    legacy = sqlite3.connect(data_directory / DATABASE_NAME)
    legacy.execute(
        "ALTER TABLE worktree_lifecycle_operations DROP COLUMN project_revision"
    )
    legacy.execute(
        "ALTER TABLE worktree_lifecycle_operations DROP COLUMN workspace_roots_json"
    )
    legacy.execute("ALTER TABLE worktrees DROP COLUMN git_dir_uid")
    legacy.execute("ALTER TABLE worktrees DROP COLUMN git_dir_inode")
    legacy.execute("ALTER TABLE worktrees DROP COLUMN git_dir_dev")
    legacy.execute("PRAGMA user_version = 45")
    legacy.commit()
    legacy.close()

    migrated = SessionStore(data_directory)
    migrated.initialize()
    try:
        root = migrated.typed_runtime_repository().read_session(session_id).roots[0]
        assert root.git_dir is not None
        assert root.git_dir_identity is not None
        assert root.git_common_dir is not None
        assert root.git_common_identity is not None

        shutil.rmtree(repository / ".git")
        _git(repository, "init", "-q", "-b", "replacement")
        _git(repository, "config", "user.name", "Eidos Tests")
        _git(repository, "config", "user.email", "eidos-tests@example.com")
        (repository / "replacement.txt").write_text(
            "replacement repository\n", encoding="utf-8"
        )
        _git(repository, "add", ".")
        _git(repository, "commit", "-qm", "replacement")

        manager = WorktreeManager(
            migrated.database,
            managed_root=tmp_path / "managed-worktrees-after-migration",
        )
        migrated_sessions = SessionApplication(
            migrated,
            scan_text=lambda value: value,
            worktree_manager=manager,
        )
        request = SessionGitStatusRequestDto(
            sessionId=session_id,
            rootId=root.id,
        )
        with pytest.raises(ApplicationError) as error:
            migrated_sessions.for_git_request(request).git_status(request)
        assert error.value.code == "WORKSPACE_IDENTITY_CHANGED"
    finally:
        migrated.close()


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
        from eidos_runtime.protocol.methods import CheckpointForkRequestDto
        forked = checkpoints.fork(CheckpointForkRequestDto(checkpointId=checkpoint.id, operationId=str(uuid4())))
        forked_session = store.typed_runtime_repository().read_session(forked.run.session_id)
        assert forked_session is not None
        assert [root.id for root in forked_session.roots] == ["root-primary", "root-secondary"]
        assert manager.source_project(forked_session.worktree_id).workspace_root == str(primary)
        assert len(manager.repository.list_projects()) == 1
        assert manager.open(worktree_id).id == worktree_id
        manager.recover()
        assert manager.open(worktree_id).id == worktree_id
        assert checkpoints.list(
            CheckpointListRequestDto(runId=run["id"])
        ).checkpoints[0].git_snapshot_id == checkpoint.git_snapshot_id
    finally:
        store.close()


def test_new_sessions_discover_git_created_after_the_project_was_saved(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store, _manager, projects, sessions = _runtime(tmp_path)
    try:
        project = _create_project(projects, [("root", workspace)], "root")
        before = sessions.create(SessionCreateRequestDto(projectId=project.id)).root
        _git(workspace, "init", "-q", "-b", "main")
        _git(workspace, "config", "user.name", "Eidos Tests")
        _git(workspace, "config", "user.email", "tests@example.com")
        (workspace / "README.md").write_text("base\n")
        _git(workspace, "add", ".")
        _git(workspace, "commit", "-qm", "base")
        after = sessions.create(SessionCreateRequestDto(projectId=project.id, executionMode="worktree")).root
        assert not before["project"]["gitAvailable"]
        assert after["project"]["gitAvailable"]
        assert after["worktree"]["projectId"] == project.id
        refreshed_project = projects.read(ProjectReadRequest(projectId=project.id))
        assert refreshed_project.git_available
        assert refreshed_project.roots[0].repository_root == str(workspace.resolve())
    finally:
        store.close()


def test_git_controls_accept_an_attached_repository_subdirectory(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "repo", "repo\n")
    child = repository / "src"
    child.mkdir()
    (child / "child.txt").write_text("child\n")
    store, _manager, projects, sessions = _runtime(tmp_path)
    try:
        project = _create_project(projects, [("subdirectory", child)], "subdirectory")
        session = sessions.create(SessionCreateRequestDto(projectId=project.id)).root
        request = SessionGitStatusRequestDto(sessionId=session["id"], rootId="subdirectory")
        result = sessions.for_git_request(request).git_status(request).root
        assert "src/child.txt" in result["untrackedFiles"]
    finally:
        store.close()


def test_session_creation_keeps_saved_git_directory_for_draft_review(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path / "repository", "base\n")
    store, _manager, projects, sessions = _runtime(tmp_path)
    try:
        project = _create_project(projects, [("root", repository)], "root")
        session = sessions.create(SessionCreateRequestDto(projectId=project.id)).root
        saved_git_dir = store.connection.execute(
            "SELECT git_dir FROM project_roots WHERE project_id=? AND id=?",
            (project.id, "root"),
        ).fetchone()[0]
        assert saved_git_dir == str((repository / ".git").resolve())

        request = SessionGitStatusRequestDto(
            sessionId=f"draft-{uuid4()}",
            projectId=project.id,
            rootId="root",
        )
        status = sessions.for_git_request(request).git_status(request).root
        assert status["head"]
        assert session["id"]
    finally:
        store.close()


def test_secondary_tools_and_shell_observation_keep_independent_root_identity(tmp_path: Path) -> None:
    from dataclasses import replace
    import threading
    from eidos_runtime.tools.workspace import ToolExecutor
    from eidos_runtime.workspace.reader import capture_workspace_identity, WorkspacePathError
    from eidos_runtime.sandbox.workspace_manifest import diff_workspace_manifests

    primary, secondary = tmp_path / "primary", tmp_path / "secondary"
    primary.mkdir()
    secondary.mkdir()
    (secondary / "file.txt").write_text("before\n")
    identity = replace(capture_workspace_identity(primary), additional_roots=(capture_workspace_identity(secondary),))
    with ToolExecutor(identity) as executor:
        listed = executor.execute("list_files", {"path": str(secondary)}, threading.Event())
        assert listed["outcome"] == "success"
        assert executor.prepare_shell(str(secondary), threading.Event()).path == secondary
        before = executor.refresh_workspace_index(threading.Event())
        subprocess.run(["/bin/sh", "-c", 'printf changed > "$1"', "sh", str(secondary / "file.txt")], check=True)
        after = executor.refresh_workspace_index(threading.Event())
        diff = diff_workspace_manifests(before, after)
        assert diff.complete and diff.changed
        assert diff.modified == (str(secondary / "file.txt"),)
        secondary.rename(tmp_path / "moved")
        secondary.mkdir()
        with pytest.raises(WorkspacePathError, match="workspace_identity_changed"):
            executor.prepare_shell(str(secondary), threading.Event())


def test_nested_workspace_roots_report_one_manifest_entry_per_file(
    tmp_path: Path,
) -> None:
    from dataclasses import replace
    import threading
    from eidos_runtime.sandbox.workspace_manifest import (
        capture_workspace_roots_manifest,
        diff_workspace_manifests,
    )
    from eidos_runtime.tools.workspace import ToolExecutor
    from eidos_runtime.workspace.reader import capture_workspace_identity

    primary = tmp_path / "primary"
    nested = primary / "nested"
    nested.mkdir(parents=True)
    changed_file = nested / "changed.txt"
    changed_file.write_text("before\n", encoding="utf-8")
    roots = (
        capture_workspace_identity(primary),
        capture_workspace_identity(nested),
    )

    before = capture_workspace_roots_manifest(roots)
    changed_file.write_text("after\n", encoding="utf-8")
    after = capture_workspace_roots_manifest(roots)
    diff = diff_workspace_manifests(before, after)

    assert before.complete and after.complete
    assert sum("nested/changed.txt" in entry.path for entry in before.entries) == 1
    assert diff.modified == ("nested/changed.txt",)

    changed_file.write_text("before-shell\n", encoding="utf-8")
    shell_identity = replace(
        roots[0], additional_roots=(roots[1],)
    )
    with ToolExecutor(shell_identity) as executor:
        shell_before = executor.refresh_workspace_index(threading.Event())
        changed_file.write_text("after-shell\n", encoding="utf-8")
        shell_after = executor.refresh_workspace_index(threading.Event())
    shell_diff = diff_workspace_manifests(shell_before, shell_after)
    assert shell_before.complete and shell_after.complete
    assert sum(
        entry.path == "nested/changed.txt" for entry in shell_before.entries
    ) == 1
    assert shell_diff.modified == ("nested/changed.txt",)


def test_real_run_start_accepts_multi_root_worktree_and_rejects_replaced_shared_root(tmp_path: Path) -> None:
    from test_managed_run_admission import _setup, _start
    repository = _repository(tmp_path / "repository", "primary\n")
    shared = tmp_path / "shared"
    shared.mkdir()
    store, manager, sessions, runs, _ = _setup(tmp_path)
    try:
        projects = ProjectApplication(manager.repository)
        project = _create_project(projects, [("primary", repository), ("shared", shared)], "primary")
        session = sessions.create(SessionCreateRequestDto(projectId=project.id, executionMode="worktree")).root
        outcome = _start(runs, session["id"])
        outcome.mark_response_delivered()
        assert store.workspace_for_run(outcome.response.root["id"]).additional_roots[0].path == shared
        store.fail_run(outcome.response.root["id"], "fixture complete")
        shared.rename(tmp_path / "displaced")
        shared.mkdir()
        with pytest.raises(ApplicationError) as error:
            _start(runs, session["id"])
        assert error.value.code == "WORKSPACE_IDENTITY_CHANGED"
    finally:
        store.close()


@pytest.mark.parametrize("execution_mode", ["local", "worktree"])
def test_forks_after_primary_switch_inherit_the_source_roots(tmp_path: Path, execution_mode: str) -> None:
    from test_session_fork_application import applications, completed_answer
    from eidos_runtime.protocol.session_fork import SessionForkRequest
    from eidos_runtime.protocol.methods import SessionHandoffRequestDto
    primary = _repository(tmp_path / "primary", "primary\n")
    secondary = _repository(tmp_path / "secondary", "secondary\n")
    store, sessions, forks = applications(tmp_path)
    manager = forks.manager
    assert manager is not None
    projects = ProjectApplication(manager.repository)
    try:
        project = _create_project(projects, [("primary", primary), ("secondary", secondary)], "primary")
        source = sessions.create(SessionCreateRequestDto(projectId=project.id, executionMode=execution_mode)).root
        item = completed_answer(store, source["id"])
        projects.update(ProjectUpdateRequest(projectId=project.id, expectedRevision=1, operationId=str(uuid4()),
            roots=[ProjectRootInput(id="secondary", path=str(secondary))], primaryRootId="secondary"))
        target = forks.fork(SessionForkRequest(operationId=str(uuid4()), sourceSessionId=source["id"],
            sourceItemId=item, target={"kind": "new_worktree"})).session
        assert target.project.id == project.id
        assert [root.id for root in target.roots] == ["primary", "secondary"]
        assert manager.source_project(target.worktree.worktree_id).workspace_root == str(primary)
        assert len(manager.repository.list_projects()) == 1
        assert not store.session_is_projectless(source["id"])
        if execution_mode == "local":
            moved = sessions.handoff(SessionHandoffRequestDto(sessionId=source["id"], target="worktree", operationId=str(uuid4()))).root
            assert moved["worktree"]["projectId"] == project.id
            assert len(manager.repository.list_projects()) == 1
    finally:
        store.close()


def test_native_sandbox_writes_secondary_root_outside_temporary_paths(tmp_path: Path) -> None:
    import tempfile
    import threading
    from eidos_runtime.sandbox.seatbelt import SeatbeltProfile, is_seatbelt_usable, run_sandboxed
    from eidos_runtime.tools.workspace import ToolExecutor
    from eidos_runtime.sandbox.permissions import BasePermissionProfile, materialize_effective_profile
    if not is_seatbelt_usable():
        pytest.skip("macOS Seatbelt unavailable")
    # A root under /tmp can accidentally inherit broad temporary-file access.
    with tempfile.TemporaryDirectory(prefix=".eidos-multi-root-test-", dir=Path.home()) as temporary:
        outside_tmp = Path(temporary).resolve()
        primary, secondary = outside_tmp / "primary", outside_tmp / "secondary"
        primary.mkdir()
        _repository(secondary, "secondary\n")
        target = secondary / "file.txt"
        target.write_text("before\n")
        store, _manager, projects, sessions = _runtime(tmp_path)
        try:
            project = _create_project(projects, [("primary", primary), ("secondary", secondary)], "primary")
            session = sessions.create(SessionCreateRequestDto(projectId=project.id)).root
            run, _ = store.create_run(session["id"], "change shared file")
            snapshot = store.read_run_resolution_snapshot(run["id"])
            effective = materialize_effective_profile(BasePermissionProfile.model_validate_json(snapshot.permission_profile_json))
            home, scratch = outside_tmp / "home", outside_tmp / "scratch"
            home.mkdir()
            scratch.mkdir()
            profile = SeatbeltProfile.create(workspace_root=primary, sandbox_home=home,
                sandbox_tmp=scratch, effective_permissions=effective)
            result = run_sandboxed(profile, ["/bin/sh", "-c", 'printf shell > "$1"', "sh", str(target)])
            assert result.returncode == 0, result.stderr
            assert target.read_text() == "shell"
            denied = run_sandboxed(profile, ["/usr/bin/touch", str(secondary / ".git" / "forbidden")])
            assert denied.returncode != 0
            assert not (secondary / ".git" / "forbidden").exists()
            with ToolExecutor(store.workspace_for_run(run["id"])) as executor:
                executor.write_permissions = effective
                patch = executor.prepare_file_change("apply_patch", {"patch":
                    f"*** Begin Patch\n*** Update File: {target}\n@@\n-shell\n+helper\n*** End Patch\n"}, threading.Event())
                assert not isinstance(patch, dict), patch
                result, _delta = executor.commit_patch("apply_patch", patch, threading.Event())
                assert result["outcome"] == "success", result
            assert target.read_bytes() == b"helper"
        finally:
            store.close()


def test_secondary_images_reject_replaced_directory_identity(tmp_path: Path) -> None:
    import threading
    from test_view_image import _png_bytes
    from eidos_runtime.tools.view_image import ViewImageTool, ViewImageRootAuthority
    from eidos_runtime.workspace.reader import capture_workspace_identity
    primary, secondary = tmp_path / "primary", tmp_path / "secondary"
    primary.mkdir()
    secondary.mkdir()
    image = secondary / "image.png"
    image.write_bytes(_png_bytes())
    authority = ViewImageRootAuthority(primary, workspace_identities=(capture_workspace_identity(primary), capture_workspace_identity(secondary)))
    tool = ViewImageTool(authority)
    assert tool.execute({"path": str(image)}, threading.Event()).result["outcome"] == "success"
    secondary.rename(tmp_path / "displaced")
    secondary.mkdir()
    image.write_bytes(_png_bytes())
    assert tool.execute({"path": str(image)}, threading.Event())["code"] == "unsafe_path"


def test_mcp_verifies_shared_root_before_startup_and_each_call(tmp_path: Path) -> None:
    import sys
    import threading
    from eidos_runtime.extensions.mcp import McpConnection, McpUnavailable
    from eidos_runtime.extensions.contracts import McpServerConfigV1
    from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
    from eidos_runtime.workspace.reader import WorkspaceReader, capture_workspace_identity

    primary, secondary = tmp_path / "primary", tmp_path / "secondary"
    primary.mkdir()
    secondary.mkdir()
    identities = (capture_workspace_identity(primary), capture_workspace_identity(secondary))
    def check() -> None:
        for identity in identities:
            with WorkspaceReader(identity):
                pass
    fixture = Path(__file__).parent / "fixtures" / "mcp_fixture.py"
    kernel = RuntimeAsyncKernel()
    kernel.start()
    config = McpServerConfigV1(id="fixture", executable=sys.executable, argv=[str(fixture)], permissionProfile="workspace_read", enabled=True)
    def connection() -> McpConnection:
        return McpConnection(plugin_root=fixture.parent, workspace_root=primary, additional_workspace_roots=(secondary,),
            workspace_check=check, config=config, on_list_changed=lambda: None, async_kernel=kernel, sandbox=False)
    first = connection()
    try:
        first.start()
        assert first.call("echo", {"message": "allowed"}, threading.Event())["outcome"] == "success"
        secondary.rename(tmp_path / "displaced")
        secondary.mkdir()
        assert first.call("echo", {"message": "forbidden"}, threading.Event())["code"] == "tool_unavailable"
        second = connection()
        try:
            with pytest.raises(McpUnavailable, match="workspace_identity_changed"):
                second.start()
        finally:
            second.close()
    finally:
        first.close()
        kernel.close()


def test_git_stage_and_comments_are_isolated_between_equal_filenames(tmp_path: Path) -> None:
    import hashlib
    from eidos_runtime.application.review import ReviewApplication
    from eidos_runtime.persistence.review_comments import ReviewCommentRepository
    from eidos_runtime.protocol.methods import SessionGitStageRequestDto, SessionGitDiffRequestDto
    from eidos_runtime.protocol.review import ReviewCommentCreateRequestDto, ReviewCommentListRequestDto
    primary = _repository(tmp_path / "primary", "primary\n")
    secondary = _repository(tmp_path / "secondary", "secondary\n")
    store, _manager, projects, sessions = _runtime(tmp_path)
    try:
        project = _create_project(projects, [("primary", primary), ("secondary", secondary)], "primary")
        session = sessions.create(SessionCreateRequestDto(projectId=project.id)).root
        for root in (primary, secondary):
            (root / "repository-marker.txt").write_text("changed\n")
        stage = SessionGitStageRequestDto(sessionId=session["id"], rootId="secondary", paths=["repository-marker.txt"], operationId=str(uuid4()))
        sessions.for_git_request(stage).git_stage(stage)
        assert not _git(primary, "diff", "--cached")
        assert "changed" in _git(secondary, "diff", "--cached")
        diff_request = SessionGitDiffRequestDto(sessionId=session["id"], rootId="secondary", scope="head", path="repository-marker.txt")
        diff = sessions.for_git_request(diff_request).git_diff(diff_request).root
        review = ReviewApplication(ReviewCommentRepository(store.database), git=sessions, scan_text=lambda value: value)
        comment = ReviewCommentCreateRequestDto(sessionId=session["id"], rootId="secondary", operationId=str(uuid4()),
            commentId=str(uuid4()), path="repository-marker.txt", scope="head", side="new", line=1,
            body="secondary review", baseHead=diff["head"], diffHash=hashlib.sha256(diff["unifiedDiff"].encode()).hexdigest())
        review.for_git_request(comment).create_comment(comment)
        for root, count in (("primary", 0), ("secondary", 1)):
            request = ReviewCommentListRequestDto(sessionId=session["id"], rootId=root)
            assert len(review.for_git_request(request).list_comments(request).comments) == count
    finally:
        store.close()


def test_primary_worktree_maps_only_its_source_checkout_subdirectories(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "repository", "primary\n")
    for directory in ("src", "docs"):
        (repository / directory).mkdir()
        (repository / directory / "file.txt").write_text(directory)
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "directories")
    linked = tmp_path / "linked"
    _git(repository, "worktree", "add", "-qb", "linked", str(linked))
    plain = tmp_path / "plain"
    plain.mkdir()
    store, manager, projects, sessions = _runtime(tmp_path)
    try:
        project = _create_project(projects, [("src", repository / "src"), ("docs", repository / "docs"), ("linked", linked), ("plain", plain)], "src")
        created = sessions.create(SessionCreateRequestDto(projectId=project.id, executionMode="worktree")).root
        root = Path(created["worktree"]["worktreeRoot"])
        identities = store.workspace_for_session(created["id"])
        assert identities.path == root / "src"
        assert [identity.path for identity in identities.additional_roots] == [root / "docs", linked, plain]
        assert [entry.get("shared", False) for entry in created["roots"]] == [False, False, True, True]
        assert len(manager.repository.list_projects()) == 1
    finally:
        store.close()


def test_worktree_private_gitdir_identity_rejects_same_path_replacement(
    tmp_path: Path,
) -> None:
    from eidos_runtime.git.errors import WorktreeError

    repository = _repository(tmp_path / "repository", "primary\n")
    store, manager, projects, sessions = _runtime(tmp_path)
    try:
        project = _create_project(
            projects,
            [("root-primary", repository)],
            "root-primary",
        )
        session = sessions.create(
            SessionCreateRequestDto(
                projectId=project.id,
                executionMode="worktree",
            )
        ).root
        worktree_id = session["worktree"]["worktreeId"]
        worktree = manager.read_worktree(worktree_id)
        assert worktree.git_dir_identity is not None
        assert store.workspace_for_session(session["id"]).git_dir_identity == (
            worktree.git_dir_identity
        )
        private_git_dir = Path(worktree.git_dir)
        displaced = private_git_dir.with_name(private_git_dir.name + "-displaced")
        private_git_dir.rename(displaced)
        shutil.copytree(displaced, private_git_dir, symlinks=True)

        with pytest.raises(WorktreeError, match="workspace_identity_changed"):
            manager.execution_identity(worktree_id)
    finally:
        store.close()


def test_worktree_root_rejects_replaced_symlink_parent(tmp_path: Path) -> None:
    from eidos_runtime.git.errors import WorktreeError

    repository = _repository(tmp_path / "repository", "primary\n")
    managed_root = tmp_path / "managed-worktrees"
    store, manager, projects, sessions = _runtime(tmp_path)
    try:
        project = _create_project(
            projects,
            [("root-primary", repository)],
            "root-primary",
        )
        session = sessions.create(
            SessionCreateRequestDto(
                projectId=project.id,
                executionMode="worktree",
            )
        ).root
        worktree_id = session["worktree"]["worktreeId"]
        displaced = tmp_path / "managed-worktrees-original"
        outside = tmp_path / "outside-worktrees"
        managed_root.rename(displaced)
        shutil.copytree(displaced, outside, symlinks=True)
        managed_root.symlink_to(outside, target_is_directory=True)

        with pytest.raises(WorktreeError, match="workspace_identity_changed"):
            manager.execution_identity(worktree_id)
    finally:
        store.close()


@pytest.mark.parametrize("missing_common_dir", ["none", "worktree", "both"])
def test_v46_migration_pins_existing_worktree_gitdir_identity(
    tmp_path: Path, missing_common_dir: str,
) -> None:
    from eidos_runtime.git.errors import WorktreeError

    repository = _repository(tmp_path / "repository", "primary\n")
    store, manager, projects, sessions = _runtime(tmp_path)
    try:
        project = _create_project(
            projects,
            [("root-primary", repository)],
            "root-primary",
        )
        session = sessions.create(
            SessionCreateRequestDto(
                projectId=project.id,
                executionMode="worktree",
            )
        ).root
        worktree_id = session["worktree"]["worktreeId"]
        connection = store.connection
        assert connection is not None
        if missing_common_dir != "none":
            connection.execute("UPDATE worktrees SET git_common_dir=NULL WHERE id=?", (worktree_id,))
        if missing_common_dir == "both":
            connection.execute("UPDATE projects SET git_repository_root=NULL, git_common_dir=NULL WHERE id=?", (project.id,))
        connection.execute("ALTER TABLE worktrees DROP COLUMN git_dir_uid")
        connection.execute("ALTER TABLE worktrees DROP COLUMN git_dir_inode")
        connection.execute("ALTER TABLE worktrees DROP COLUMN git_dir_dev")
        connection.execute("PRAGMA user_version=46")
        connection.commit()
        data_directory = store.data_directory
        assert data_directory is not None
        store.close()

        migrated = SessionStore(data_directory)
        migrated.initialize()
        assert migrated.health_state == "ready", migrated.health_code
        migrated_manager = WorktreeManager(
            migrated.database,
            managed_root=tmp_path / "managed-worktrees",
        )
        worktree = migrated_manager.read_worktree(worktree_id)
        if missing_common_dir == "both":
            assert worktree.git_dir_identity is None
            with pytest.raises(WorktreeError):
                migrated_manager.execution_identity(worktree_id)
            return
        assert worktree.git_dir_identity is not None
        private_git_dir = Path(worktree.git_dir)
        displaced = private_git_dir.with_name(private_git_dir.name + "-displaced")
        private_git_dir.rename(displaced)
        shutil.copytree(displaced, private_git_dir, symlinks=True)

        with pytest.raises(WorktreeError, match="workspace_identity_changed"):
            migrated_manager.execution_identity(worktree_id)
    finally:
        store.close()


def test_worktree_git_status_accepts_the_frozen_checkout(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "repository", "base\n")
    store, _manager, projects, sessions = _runtime(tmp_path)
    try:
        project = _create_project(projects, [("root", repository)], "root")
        session = sessions.create(SessionCreateRequestDto(projectId=project.id, executionMode="worktree")).root
        request = SessionGitStatusRequestDto(sessionId=session["id"], rootId="root")
        status = sessions.for_git_request(request).git_status(request).root
        assert status["head"] == _git(repository, "rev-parse", "HEAD")
    finally:
        store.close()


def test_worktree_rejects_symlinked_parent_of_mapped_secondary_root(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "repository", "primary\n")
    nested = repository / "docs" / "sub"
    nested.mkdir(parents=True)
    (nested / "file.txt").write_text("source", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "nested workspace")
    outside = tmp_path / "outside" / "docs" / "sub"
    outside.mkdir(parents=True)
    (outside / "outside.txt").write_text("outside", encoding="utf-8")
    store, manager, projects, sessions = _runtime(tmp_path)
    explorer = WorkspaceExplorerApplication(
        store.typed_runtime_repository(),
        worktree_manager=manager,
        scan_text=lambda value: value,
    )
    try:
        project = _create_project(
            projects,
            [("primary", repository), ("nested", nested)],
            "primary",
        )
        session = sessions.create(
            SessionCreateRequestDto(projectId=project.id, executionMode="worktree")
        ).root
        worktree_root = Path(session["worktree"]["worktreeRoot"])
        mapped_parent = worktree_root / "docs"
        mapped_parent.rename(worktree_root / "docs-original")
        mapped_parent.symlink_to(outside.parent, target_is_directory=True)

        with pytest.raises(WorkspaceIdentityChangedError):
            store.workspace_for_session(session["id"])
        with pytest.raises(WorkspaceIdentityChangedError):
            store.create_run(session["id"], "write through a replaced root")
        with pytest.raises(ApplicationError) as error:
            explorer.list_directory(
                WorkspaceListDirectoryRequestDto(
                    sessionId=session["id"],
                    workspaceRoot=str(worktree_root / "docs" / "sub"),
                    path=".",
                )
            )
        assert error.value.code == "WORKSPACE_IDENTITY_CHANGED"
    finally:
        store.close()


def test_legacy_worktree_session_create_replay_accepts_added_project_id(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path / "repository", "primary\n")
    secondary = repository / "docs"
    secondary.mkdir()
    (secondary / "file.txt").write_text("secondary", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "secondary root")
    store, manager, projects, sessions = _runtime(tmp_path)
    try:
        project = _create_project(projects, [("primary", repository)], "primary")
        operation_id = str(uuid4())
        created = sessions.create(
            SessionCreateRequestDto(
                workspaceRoot=str(repository),
                executionMode="worktree",
                operationId=operation_id,
            )
        )

        projects.update(
            ProjectUpdateRequest(
                projectId=project.id,
                operationId=str(uuid4()),
                expectedRevision=1,
                roots=[
                    ProjectRootInput(id="primary", path=str(repository)),
                    ProjectRootInput(id="secondary", path=str(secondary)),
                ],
                primaryRootId="secondary",
            )
        )

        replay = sessions.create(
            SessionCreateRequestDto(
                projectId=project.id,
                executionMode="worktree",
                operationId=operation_id,
            )
        )

        assert replay.id == created.id
        assert replay.worktree == created.worktree
        assert len(manager.repository.list_worktrees()) == 1
    finally:
        store.close()


@pytest.mark.parametrize("restart", [False, True])
def test_worktree_creation_freezes_project_roots_before_concurrent_edit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restart: bool) -> None:
    primary = _repository(tmp_path / "primary", "primary\n")
    secondary = _repository(tmp_path / "secondary", "secondary\n")
    store, manager, projects, sessions = _runtime(tmp_path)
    project = _create_project(projects, [("primary", primary), ("secondary", secondary)], "primary")
    operation = str(uuid4())
    request = SessionCreateRequestDto(projectId=project.id, executionMode="worktree", operationId=operation)
    real_create = manager.create_prepared
    class Crash(BaseException):
        pass
    def create_then_edit(*args, **kwargs):
        worktree = real_create(*args, **kwargs)
        projects.update(ProjectUpdateRequest(projectId=project.id, expectedRevision=1, operationId=str(uuid4()),
            roots=[ProjectRootInput(id="secondary", path=str(secondary))], primaryRootId="secondary"))
        if restart:
            raise Crash()
        return worktree
    monkeypatch.setattr(manager, "create_prepared", create_then_edit)
    try:
        if restart:
            with pytest.raises(Crash):
                sessions.create(request)
            store.close()
            store, manager, projects, sessions = _runtime(tmp_path)
            manager.recover_lifecycle()
        created = sessions.create(request).root
        identity = store.workspace_for_session(created["id"])
        assert created["workspaceRoot"] == str(primary)
        assert [root["id"] for root in created["roots"]] == ["primary", "secondary"]
        assert identity.path == Path(created["worktree"]["worktreeRoot"])
        assert identity.additional_roots[0].path == secondary
        assert projects.read(ProjectReadRequest(projectId=project.id)).workspace_root == str(secondary)
        assert manager.lifecycle.read("session/create", operation).project_revision == 1
        assert len(manager.repository.list_worktrees()) == 1
    finally:
        store.close()


def test_git_context_reads_do_not_create_projects_for_attached_checkouts(tmp_path: Path) -> None:
    from eidos_runtime.protocol.methods import GitContextRequestDto
    repository = _repository(tmp_path / "repository", "primary\n")
    linked = tmp_path / "linked"
    _git(repository, "worktree", "add", "-qb", "linked", str(linked))
    store, manager, projects, sessions = _runtime(tmp_path)
    try:
        _create_project(projects, [("main", repository), ("linked", linked)], "main")
        assert sessions.git_context(GitContextRequestDto(workspaceRoot=str(linked))).git_available
        assert len(manager.repository.list_projects()) == 1
    finally:
        store.close()


def test_native_managed_subdirectories_remain_writable_inside_protected_data(tmp_path: Path) -> None:
    from eidos_runtime.sandbox.seatbelt import SeatbeltProfile, is_seatbelt_usable, run_sandboxed
    from eidos_runtime.sandbox.permissions import BasePermissionProfile, materialize_effective_profile
    if not is_seatbelt_usable():
        pytest.skip("macOS Seatbelt unavailable")
    repository = _repository(tmp_path / "repository", "primary\n")
    for name in ("src", "docs"):
        (repository / name).mkdir()
        (repository / name / "file.txt").write_text("source")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "directories")
    store = SessionStore(tmp_path / "data")
    store.initialize()
    manager = WorktreeManager(store.database)
    projects = ProjectApplication(manager.repository)
    sessions = SessionApplication(store, scan_text=lambda value: value, worktree_manager=manager)
    try:
        project = _create_project(projects, [("src", repository / "src"), ("docs", repository / "docs")], "src")
        session = sessions.create(SessionCreateRequestDto(projectId=project.id, executionMode="worktree")).root
        run, _ = store.create_run(session["id"], "write")
        identity = store.workspace_for_run(run["id"])
        snapshot = store.read_run_resolution_snapshot(run["id"])
        effective = materialize_effective_profile(BasePermissionProfile.model_validate_json(snapshot.permission_profile_json))
        home, scratch = tmp_path / "home", tmp_path / "scratch"
        home.mkdir()
        scratch.mkdir()
        profile = SeatbeltProfile.create(workspace_root=identity.path, sandbox_home=home, sandbox_tmp=scratch, effective_permissions=effective)
        for root in (identity, *identity.additional_roots):
            result = run_sandboxed(profile, ["/usr/bin/touch", str(root.path / "created")])
            assert result.returncode == 0, result.stderr
        result = run_sandboxed(profile, ["/usr/bin/touch", str(store.data_directory / "forbidden")])
        assert result.returncode != 0
        assert not (store.data_directory / "forbidden").exists()
    finally:
        store.close()


def test_worktree_creation_uses_one_project_snapshot_across_the_initial_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    primary = _repository(tmp_path / "primary", "primary\n")
    secondary = _repository(tmp_path / "secondary", "secondary\n")
    store, manager, projects, sessions = _runtime(tmp_path)
    try:
        project = _create_project(projects, [("primary", primary), ("secondary", secondary)], "primary")
        original = manager.project
        calls = []
        def read_then_edit(identifier):
            saved = original(identifier)
            calls.append(identifier)
            if len(calls) == 1:
                projects.update(ProjectUpdateRequest(projectId=project.id, expectedRevision=1, operationId=str(uuid4()),
                    roots=[ProjectRootInput(id="secondary", path=str(secondary))], primaryRootId="secondary"))
            return saved
        monkeypatch.setattr(manager, "project", read_then_edit)
        result = sessions.create(SessionCreateRequestDto(projectId=project.id, workspaceRoot=str(primary), executionMode="worktree")).root
        assert result["workspaceRoot"] == str(primary)
        assert result["worktree"]["repositoryRoot"] == str(primary)
        assert calls == [project.id]
    finally:
        store.close()


def test_worktree_retry_rejects_another_subdirectory_of_the_same_repository(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "repository", "primary\n")
    for name in ("a", "b"):
        (repository / name).mkdir()
        (repository / name / "file.txt").write_text(name)
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "directories")
    store, manager, projects, sessions = _runtime(tmp_path)
    try:
        project = _create_project(projects, [("a", repository / "a"), ("b", repository / "b")], "a")
        operation_id = str(uuid4())
        request = SessionCreateRequestDto(projectId=project.id, workspaceRoot=str(repository / "a"), executionMode="worktree", operationId=operation_id)
        created = sessions.create(request)
        projects.update(ProjectUpdateRequest(projectId=project.id, expectedRevision=1, operationId=str(uuid4()),
            roots=[ProjectRootInput(id="a", path=str(repository / "a")), ProjectRootInput(id="b", path=str(repository / "b"))], primaryRootId="b"))
        with pytest.raises(ApplicationError) as error:
            sessions.create(request.model_copy(update={"workspace_root": str(repository / "b")}))
        assert error.value.code == "OPERATION_ID_REUSED"
        assert sessions.create(request).id == created.id
        assert len(manager.repository.list_worktrees()) == 1
    finally:
        store.close()


def test_retry_after_the_lifecycle_is_prepared_waits_for_the_same_operation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading
    repository = _repository(tmp_path / "repository", "primary\n")
    store, manager, projects, sessions = _runtime(tmp_path)
    release, entered, retry_started, retry_done = (threading.Event() for _ in range(4))
    errors, results, calls = [], [], []
    original = manager.create_prepared
    def blocked(*args, **kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(timeout=5)
        return original(*args, **kwargs)
    monkeypatch.setattr(manager, "create_prepared", blocked)
    project = _create_project(projects, [("primary", repository)], "primary")
    request = SessionCreateRequestDto(projectId=project.id, executionMode="worktree", operationId=str(uuid4()))
    def create(retry=False):
        try:
            if retry:
                retry_started.set()
            results.append(sessions.create(request).id)
        except BaseException as error:
            errors.append(error)
        finally:
            if retry:
                retry_done.set()
    first, second = threading.Thread(target=create), threading.Thread(target=create, args=(True,))
    try:
        first.start()
        assert entered.wait(timeout=5)
        second.start()
        assert retry_started.wait(timeout=5)
        assert not retry_done.wait(timeout=.1)
        assert len(calls) == 1
        release.set()
        first.join(timeout=5)
        second.join(timeout=5)
        assert not first.is_alive() and not second.is_alive()
        assert not errors, errors
        assert len(results) == 2 and results[0] == results[1]
        assert len(calls) == 1
    finally:
        release.set()
        first.join(timeout=5)
        if second.ident is not None:
            second.join(timeout=5)
        store.close()


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("replacement", ["root", "git_metadata"])
def test_worktree_creation_rejects_replaced_source_before_git_effects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restart: bool, replacement: str) -> None:
    import shutil
    repository = _repository(tmp_path / "repository", "primary\n")
    store, manager, projects, sessions = _runtime(tmp_path)
    project = _create_project(projects, [("primary", repository)], "primary")
    request = SessionCreateRequestDto(projectId=project.id, executionMode="worktree", operationId=str(uuid4()))
    original = manager.create_prepared
    class Crash(BaseException):
        pass
    def replace_source():
        target = repository if replacement == "root" else repository / ".git"
        displaced = tmp_path / "displaced"
        target.rename(displaced)
        shutil.copytree(displaced, target)
    def intercepted(*args, **kwargs):
        if restart:
            raise Crash()
        replace_source()
        return original(*args, **kwargs)
    monkeypatch.setattr(manager, "create_prepared", intercepted)
    try:
        if restart:
            with pytest.raises(Crash):
                sessions.create(request)
            store.close()
            replace_source()
            store, manager, projects, sessions = _runtime(tmp_path)
        with pytest.raises(ApplicationError) as error:
            sessions.create(request)
        assert error.value.code == "WORKSPACE_IDENTITY_CHANGED"
        assert not manager.repository.list_worktrees()
        assert "managed-worktrees" not in _git(repository, "worktree", "list", "--porcelain")
    finally:
        store.close()
