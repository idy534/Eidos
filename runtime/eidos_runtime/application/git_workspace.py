from __future__ import annotations

from copy import copy
from pathlib import Path

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.domain.session import Session, SessionExecutionMode, SessionTaskStatus
from eidos_runtime.domain.project_workspace import DirectoryIdentity, SessionRoot
from eidos_runtime.git.errors import WorktreeError
from eidos_runtime.protocol.methods import SessionGitDiffRequestDto, SessionGitStatusRequestDto
from eidos_runtime.workspace.reader import (
    capture_directory_identity,
    WorkspacePathError,
    WorkspaceReader,
)


class ScopedGitSessionRepository:
    """Keep the existing Git lifecycle while selecting one persisted checkout."""

    def __init__(self, repository, selected: Session) -> None:
        self.repository = repository
        self.selected = selected

    def read_session(self, session_id: str) -> Session | None:
        return self.selected if session_id == self.selected.id else self.repository.read_session(session_id)

    def assert_session_deletable(self, session_id: str) -> None:
        self.repository.assert_session_deletable(session_id)

    def has_active_run_for_workspace(self, workspace_root: str) -> bool:
        return self.repository.has_active_run_for_workspace(workspace_root)

    def __getattr__(self, name: str):
        return getattr(self.repository, name)


def scoped_git_application(application, request):
    root_id = getattr(request, "root_id", None)
    if isinstance(request, (SessionGitStatusRequestDto, SessionGitDiffRequestDto)) and request.session_id.startswith("draft-"):
        session = _draft_git_session(application, request)
        root_id = session.roots[0].id
    elif root_id is None:
        return application
    else:
        session = application._repository.read_session(request.session_id)
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
        if (
            worktree_identity.git_common_dir
            != str(Path(root.git_common_dir).resolve(strict=False))
        ):
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
    if (discovery.repository_root, discovery.git_common_dir) != (root.repository_root, root.git_common_dir):
        raise ApplicationError("WORKSPACE_IDENTITY_CHANGED")
    selected = session.model_copy(update={
        "workspace_root": root.repository_root, "execution_mode": SessionExecutionMode.LOCAL,
        "worktree_id": None, "associated_worktree_id": None, "roots": (root,),
    })
    from eidos_runtime.application.git_workflow import GitWorkflowApplication
    result = copy(application)
    result._repository = ScopedGitSessionRepository(application._repository, selected)
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


def _draft_git_session(application, request: SessionGitStatusRequestDto | SessionGitDiffRequestDto) -> Session:
    """Build a read-only view from the saved Project without creating a Session."""
    if request.project_id is None:
        raise ApplicationError("INVALID_PARAMS")
    manager = application._require_manager()
    try:
        project = manager.project(request.project_id)
    except WorktreeError as error:
        raise ApplicationError("RESOURCE_NOT_FOUND") from error
    root_id = request.root_id or project.primary_root_id
    root = next((entry for entry in project.roots if entry.id == root_id), None)
    if root is None or root.repository_root is None:
        raise ApplicationError("WORKSPACE_BOUNDARY_VIOLATION", "Git target is not an attached repository")
    try:
        identity = capture_directory_identity(root.path)
        discovery = manager.discover(identity.path)
        if (
            discovery.repository_root != root.repository_root
            or discovery.git_dir != root.git_dir
            or discovery.git_common_dir != root.git_common_dir
        ):
            raise WorkspacePathError("workspace_identity_changed")

        def metadata_identity(path: str | None) -> DirectoryIdentity:
            if path is None:
                raise WorkspacePathError("workspace_identity_changed")
            captured = capture_directory_identity(path)
            with WorkspaceReader(captured):
                return DirectoryIdentity(
                    device=captured.device,
                    inode=captured.inode,
                    owner=captured.owner,
                )

        git_dir_identity = metadata_identity(discovery.git_dir)
        git_common_identity = metadata_identity(discovery.git_common_dir)
    except (WorkspacePathError, WorktreeError) as error:
        raise ApplicationError("WORKSPACE_IDENTITY_CHANGED") from error
    selected = SessionRoot(
        id=root.id, path=root.path, repository_root=root.repository_root,
        git_dir=root.git_dir, git_common_dir=root.git_common_dir,
        git_dir_identity=git_dir_identity,
        git_common_identity=git_common_identity,
        device=identity.device, inode=identity.inode, owner=identity.owner,
    )
    return Session(
        id=request.session_id, project_id=project.id, roots=(selected,),
        workspace_root=root.path, task_status=SessionTaskStatus.NEW,
        created_at=project.created_at, updated_at=project.updated_at,
    )
