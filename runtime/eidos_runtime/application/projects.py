from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.session_lifecycle import SessionLifecycleCoordinator
from eidos_runtime.db.errors import (
    OperationConflictError,
    OperationInProgressError,
    ProjectHasSessionsError,
    ProjectWorktreeRecoveryRequiredError,
    ResourceNotFoundError,
    StorageError,
)
from eidos_runtime.domain.project import Project
from eidos_runtime.git.errors import WorktreeError
from eidos_runtime.persistence.worktrees import ProjectWorktreeRepository
from eidos_runtime.protocol.methods import (
    ProjectCreateRequestDto,
    ProjectCreateResponseDto,
    ProjectDeleteRequestDto,
    ProjectDeleteResponseDto,
    ProjectListRequestDto,
    ProjectListResponseDto,
)
from eidos_runtime.protocol.schemas import ProjectDto
from eidos_runtime.protocol.project_workspaces import ProjectReadRequest, ProjectUpdateRequest, ProjectRootDto, ProjectCreateFoldersRequest
from eidos_runtime.persistence.project_workspaces import ProjectWorkspaceRepository, ProjectConfigurationConflict
from eidos_runtime.domain.project_workspace import ProjectRoot


class ProjectApplication:
    """Owns the small Project catalog and metadata deletion use cases."""

    def __init__(
        self,
        repository: ProjectWorktreeRepository,
        *,
        lifecycle: SessionLifecycleCoordinator | None = None,
        create_project: Callable[[str, str | None], Project] | None = None,
        cleanup_empty_sessions: Callable[[str], None] | None = None,
    ) -> None:
        self._repository = repository
        self._workspaces = ProjectWorkspaceRepository(repository.database)
        self._lifecycle = lifecycle or SessionLifecycleCoordinator()
        self._create_project = create_project
        self._cleanup_empty_sessions = cleanup_empty_sessions

    def create(self, request: ProjectCreateRequestDto) -> ProjectCreateResponseDto:
        name = request.name.strip() if request.name else None
        try:
            project = (
                self._create_project(request.workspace_root, name)
                if self._create_project is not None
                else self._repository.get_or_create_project(
                    request.workspace_root, name=name
                )
            )
        except WorktreeError as error:
            raise ApplicationError("PROJECT_WORKSPACE_INVALID", str(error)) from error
        except (OSError, ValueError, StorageError) as error:
            raise ApplicationError("PROJECT_PERSISTENCE_FAILED", str(error)) from error
        return ProjectCreateResponseDto.model_validate(self._dto(project).model_dump(by_alias=True))

    @staticmethod
    def _dto(project: Project) -> ProjectDto:
        return ProjectDto(
            id=project.id, name=project.name, workspaceRoot=project.workspace_root,
            gitAvailable=project.has_git, revision=project.revision,
            primaryRootId=project.primary_root_id,
            roots=[ProjectRootDto(id=root.id, path=root.path, repositoryRoot=root.repository_root) for root in sorted(project.roots, key=lambda root: root.id != project.primary_root_id)],
            createdAt=int(project.created_at.timestamp() * 1000),
            updatedAt=int(project.updated_at.timestamp() * 1000),
        )

    def list(self, _request: ProjectListRequestDto) -> ProjectListResponseDto:
        return ProjectListResponseDto(items=[self._dto(project) for project in self._repository.list_projects()])

    def read(self, request: ProjectReadRequest) -> ProjectDto:
        try:
            return self._dto(self._workspaces.read(request.project_id))
        except ResourceNotFoundError as error:
            raise ApplicationError("RESOURCE_NOT_FOUND", str(error)) from error

    def create_folders(self, request: ProjectCreateFoldersRequest) -> ProjectDto:
        try:
            roots = self._validated_roots(request.roots)
            return self._dto(self._workspaces.create(
                name=request.name.strip() if request.name else None, roots=roots,
                primary_root_id=request.primary_root_id, operation_id=request.operation_id,
            ).value)
        except OperationConflictError as error:
            raise ApplicationError("OPERATION_ID_REUSED", str(error)) from error
        except (OSError, ValueError, WorktreeError) as error:
            raise ApplicationError("PROJECT_WORKSPACE_INVALID", str(error)) from error

    def _validated_roots(self, entries) -> tuple[ProjectRoot, ...]:
        from eidos_runtime.git.discovery import GitRepositoryDiscoveryService
        from eidos_runtime.git.backend import DulwichGitBackend
        from eidos_runtime.workspace.reader import capture_workspace_identity
        roots = []
        discovery_service = GitRepositoryDiscoveryService(DulwichGitBackend())
        for entry in entries:
            identity = capture_workspace_identity(entry.path)
            if self._repository.database.workspace_overlaps_data(identity.path):
                raise ValueError("project folder overlaps Eidos data")
            git = discovery_service.resolve(identity.path)
            roots.append(ProjectRoot(
                id=entry.id, path=str(identity.path), repository_root=git.repository_root if git else None,
                git_dir=git.git_dir if git else None, git_common_dir=git.git_common_dir if git else None,
            ))
        return tuple(roots)

    def update(self, request: ProjectUpdateRequest) -> ProjectDto:
        try:
            roots = self._validated_roots(request.roots)
            mutation = self._workspaces.update(
                request.project_id, expected_revision=request.expected_revision,
                name=request.name.strip() if request.name else None, roots=tuple(roots),
                primary_root_id=request.primary_root_id, operation_id=request.operation_id,
            )
            return self._dto(mutation.value)
        except ProjectConfigurationConflict as error:
            raise ApplicationError("PROJECT_CONFIGURATION_CONFLICT", str(error)) from error
        except ResourceNotFoundError as error:
            raise ApplicationError("RESOURCE_NOT_FOUND", str(error)) from error
        except OperationConflictError as error:
            raise ApplicationError("OPERATION_ID_REUSED", str(error)) from error
        except (OSError, ValueError, WorktreeError) as error:
            raise ApplicationError("PROJECT_WORKSPACE_INVALID", str(error)) from error

    def delete(self, request: ProjectDeleteRequestDto) -> ProjectDeleteResponseDto:
        operation_guard = (
            self._lifecycle.hold_operation("project/delete", request.operation_id)
            if request.operation_id is not None
            else nullcontext()
        )
        try:
            with operation_guard:
                if self._cleanup_empty_sessions is not None:
                    self._cleanup_empty_sessions(request.project_id)
                mutation = self._repository.delete_project(
                    request.project_id,
                    operation_id=request.operation_id,
                )
        except ResourceNotFoundError as error:
            raise ApplicationError("RESOURCE_NOT_FOUND", str(error)) from error
        except ProjectHasSessionsError as error:
            raise ApplicationError("PROJECT_HAS_SESSIONS", str(error)) from error
        except ProjectWorktreeRecoveryRequiredError as error:
            raise ApplicationError(
                "PROJECT_WORKTREE_RECOVERY_REQUIRED", str(error)
            ) from error
        except StorageError as error:
            raise ApplicationError("PROJECT_PERSISTENCE_FAILED", str(error)) from error
        except OperationConflictError as error:
            raise ApplicationError("OPERATION_ID_REUSED", str(error)) from error
        except OperationInProgressError as error:
            raise ApplicationError("OPERATION_IN_PROGRESS", str(error)) from error
        return ProjectDeleteResponseDto(
            deletedProjectId=mutation.value.deleted_project_id
        )


__all__ = ["ProjectApplication"]
