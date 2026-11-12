from __future__ import annotations

from copy import copy
from pathlib import Path
from typing import TYPE_CHECKING

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.domain.session import Session, SessionExecutionMode, SessionTaskStatus
from eidos_runtime.domain.project import Project
from eidos_runtime.domain.project_workspace import DirectoryIdentity, SessionRoot
from eidos_runtime.git.errors import WorktreeError
from eidos_runtime.protocol.methods import (
    SessionGitDiffRequestDto,
    SessionGitStatusRequestDto,
    SessionGitSwitchBranchRequestDto,
)
from eidos_runtime.workspace.reader import (
    capture_directory_identity,
    WorkspacePathError,
)

if TYPE_CHECKING:
    from eidos_runtime.application.sessions import ManagedWorktreePort, SessionApplication


class ScopedGitSessionRepository:
    """Keep the existing Git lifecycle while selecting one persisted checkout."""

    def __init__(self, repository, selected: Session, *, draft: bool, source: Session) -> None:
        self.repository = repository
        self.selected = selected
        self.draft = draft
        self.source = source

    def read_session(self, session_id: str) -> Session | None:
        return self.selected if session_id == self.selected.id else self.repository.read_session(session_id)

    def assert_session_deletable(self, session_id: str) -> None:
        if self.draft and session_id == self.selected.id:
            return
        self.repository.assert_session_deletable(session_id)
        if session_id == self.source.id:
            current = self.repository.read_session(session_id)
            if current is None or current.worktree_id != self.source.worktree_id:
                raise ApplicationError("WORKSPACE_IDENTITY_CHANGED")

    def has_active_run_for_workspace(self, workspace_root: str) -> bool:
        return self.repository.has_active_run_for_workspace(workspace_root)

    def __getattr__(self, name: str):
        return getattr(self.repository, name)


def scoped_git_application(application, request):
    root_id = getattr(request, "root_id", None)
    repository = application._repository
    if isinstance(repository, ScopedGitSessionRepository):
        repository = repository.repository
    draft = isinstance(
        request, (SessionGitStatusRequestDto, SessionGitDiffRequestDto, SessionGitSwitchBranchRequestDto)
    ) and request.session_id.startswith("draft-")
    if draft:
        session = _draft_git_session(application, request)
        root_id = session.roots[0].id
    elif root_id is None:
        return application
    else:
        session = repository.read_session(request.session_id)
    if session is None:
        raise ApplicationError("RESOURCE_NOT_FOUND")
    root = next((root for root in session.roots if root.id == root_id), None)
    if root is None or root.repository_root is None:
        raise ApplicationError("WORKSPACE_BOUNDARY_VIOLATION", "Git target is not an attached repository")
    manager = application._require_manager()
    if (
        root.git_dir is None
        or root.git_common_dir is None
        or root.git_dir_identity is None
        or root.git_common_identity is None
    ):
        raise ApplicationError("WORKSPACE_IDENTITY_CHANGED")
    if session.worktree_id is not None and session.roots and root.repository_root == session.roots[0].repository_root:
        try:
            worktree_identity = manager.execution_identity(session.worktree_id)
            _verify_git_metadata_identity(
                root.git_common_dir, root.git_common_identity
            )
        except (WorkspacePathError, WorktreeError) as error:
            raise ApplicationError("WORKSPACE_IDENTITY_CHANGED") from error
        if worktree_identity.git_common_dir != Path(root.git_common_dir).resolve(strict=False):
            raise ApplicationError("WORKSPACE_IDENTITY_CHANGED")
        return application
    try:
        identity = capture_directory_identity(root.path)
        _verify_git_metadata_identity(root.git_dir, root.git_dir_identity)
        _verify_git_metadata_identity(root.git_common_dir, root.git_common_identity)
        discovery = manager.discover(root.path)
    except (WorkspacePathError, WorktreeError) as error:
        raise ApplicationError("WORKSPACE_IDENTITY_CHANGED") from error
    if (identity.device, identity.inode, identity.owner) != (root.device, root.inode, root.owner):
        raise ApplicationError("WORKSPACE_IDENTITY_CHANGED")
    if (
        discovery.repository_root,
        discovery.git_dir,
        discovery.git_common_dir,
    ) != (root.repository_root, root.git_dir, root.git_common_dir):
        raise ApplicationError("WORKSPACE_IDENTITY_CHANGED")
    selected = session.model_copy(update={
        "workspace_root": root.repository_root, "execution_mode": SessionExecutionMode.LOCAL,
        "worktree_id": None, "associated_worktree_id": None, "roots": (root,),
    })
    from eidos_runtime.application.git_workflow import GitWorkflowApplication
    result = copy(application)
    result._repository = ScopedGitSessionRepository(repository, selected, draft=draft, source=session)
    result._git_workflow = GitWorkflowApplication(result._repository, manager)
    return result


def _verify_git_metadata_identity(
    path: str | None, expected: DirectoryIdentity | None
) -> None:
    if path is None or expected is None:
        return
    actual = capture_directory_identity(path)
    if (
        actual.path != Path(path)
        or (actual.device, actual.inode, actual.owner)
        != (expected.device, expected.inode, expected.owner)
    ):
        raise WorkspacePathError("workspace_identity_changed")


def resolve_project_git_root(
    manager: ManagedWorktreePort, project_id: str, root_id: str | None, *, primary_only: bool,
) -> tuple[Project, SessionRoot]:
    """Observe Git for one attached directory without creating a Session."""
    try:
        project = manager.project(project_id)
    except WorktreeError as error:
        raise ApplicationError("RESOURCE_NOT_FOUND") from error
    root_id = root_id or project.primary_root_id
    if primary_only and root_id != project.primary_root_id:
        raise ApplicationError("WORKSPACE_BOUNDARY_VIOLATION", "Git target is not the primary project directory")
    root = next((entry for entry in project.roots if entry.id == root_id), None)
    if root is None:
        raise ApplicationError("WORKSPACE_BOUNDARY_VIOLATION", "Git target is not an attached directory")
    try:
        identity = capture_directory_identity(root.path)
        try:
            discovery = manager.discover(identity.path)
        except WorktreeError as error:
            if error.code != "not_a_git_repository":
                raise
            discovery = None
        if root.repository_root is not None:
            if discovery is None or (
                discovery.repository_root, discovery.git_dir, discovery.git_common_dir
            ) != (root.repository_root, root.git_dir, root.git_common_dir):
                raise WorkspacePathError("workspace_identity_changed")
        git_dir_identity = _capture_metadata_identity(discovery.git_dir) if discovery else None
        git_common_identity = _capture_metadata_identity(discovery.git_common_dir) if discovery else None
    except (WorkspacePathError, WorktreeError) as error:
        raise ApplicationError("WORKSPACE_IDENTITY_CHANGED") from error
    selected = SessionRoot(
        id=root.id, path=root.path,
        repository_root=discovery.repository_root if discovery else None,
        git_dir=discovery.git_dir if discovery else None,
        git_common_dir=discovery.git_common_dir if discovery else None,
        git_dir_identity=git_dir_identity,
        git_common_identity=git_common_identity,
        device=identity.device, inode=identity.inode, owner=identity.owner,
    )
    return project, selected


def _capture_metadata_identity(path: str) -> DirectoryIdentity:
    captured = capture_directory_identity(path)
    return DirectoryIdentity(device=captured.device, inode=captured.inode, owner=captured.owner)


def _draft_git_session(
    application: SessionApplication,
    request: SessionGitStatusRequestDto | SessionGitDiffRequestDto | SessionGitSwitchBranchRequestDto,
) -> Session:
    if request.project_id is None:
        raise ApplicationError("INVALID_PARAMS")
    project, selected = resolve_project_git_root(
        application._require_manager(), request.project_id, request.root_id,
        primary_only=isinstance(request, SessionGitSwitchBranchRequestDto),
    )
    return Session(
        id=request.session_id, project_id=project.id, roots=(selected,),
        workspace_root=selected.path, task_status=SessionTaskStatus.NEW,
        created_at=project.created_at, updated_at=project.updated_at,
    )
