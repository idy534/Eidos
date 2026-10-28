from __future__ import annotations

from copy import copy

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.domain.session import Session, SessionExecutionMode


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
    if root_id is None:
        return application
    session = application._repository.read_session(request.session_id)
    if session is None:
        raise ApplicationError("RESOURCE_NOT_FOUND")
    root = next((root for root in session.roots if root.id == root_id), None)
    if root is None or root.repository_root is None:
        raise ApplicationError("WORKSPACE_BOUNDARY_VIOLATION", "Git target is not an attached repository")
    from eidos_runtime.workspace.reader import capture_workspace_identity
    identity = capture_workspace_identity(root.path)
    if (identity.device, identity.inode, identity.owner) != (root.device, root.inode, root.owner):
        raise ApplicationError("WORKSPACE_IDENTITY_CHANGED")
    manager = application._require_manager()
    discovery = manager.discover(root.path)
    if (discovery.repository_root, discovery.git_common_dir) != (root.repository_root, root.git_common_dir):
        raise ApplicationError("WORKSPACE_IDENTITY_CHANGED")
    if not any(entry.path == root.repository_root for entry in session.roots):
        raise ApplicationError("WORKSPACE_BOUNDARY_VIOLATION", "Attach the repository root to use repository-wide Git controls")
    if session.worktree_id is not None and session.roots and root.repository_root == session.roots[0].repository_root:
        return application
    selected = session.model_copy(update={
        "workspace_root": root.repository_root, "execution_mode": SessionExecutionMode.LOCAL,
        "worktree_id": None, "associated_worktree_id": None, "roots": (root,),
    })
    from eidos_runtime.application.git_workflow import GitWorkflowApplication
    result = copy(application)
    result._repository = ScopedGitSessionRepository(application._repository, selected)
    result._git_workflow = GitWorkflowApplication(result._repository, manager)
    return result
