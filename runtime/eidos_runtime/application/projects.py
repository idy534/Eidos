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


class ProjectApplication:
    """Owns the small Project catalog and metadata deletion use cases."""

    def __init__(
        self,
        repository: ProjectWorktreeRepository,
        *,
        lifecycle: SessionLifecycleCoordinator | None = None,
        create_project: Callable[[str, str], Project] | None = None,
        cleanup_empty_sessions: Callable[[str], None] | None = None,
    ) -> None:
        self._repository = repository
        self._lifecycle = lifecycle or SessionLifecycleCoordinator()
        self._create_project = create_project
        self._cleanup_empty_sessions = cleanup_empty_sessions

    def create(self, request: ProjectCreateRequestDto) -> ProjectCreateResponseDto:
        name = request.name.strip()
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
        return ProjectCreateResponseDto(
            id=project.id,
            name=project.name,
            workspaceRoot=project.workspace_root,
            gitAvailable=project.has_git,
            createdAt=int(project.created_at.timestamp() * 1000),
            updatedAt=int(project.updated_at.timestamp() * 1000),
        )

    def list(self, _request: ProjectListRequestDto) -> ProjectListResponseDto:
        return ProjectListResponseDto(
            items=[
                ProjectDto(
                    id=project.id,
                    name=project.name,
                    workspaceRoot=project.workspace_root,
                    gitAvailable=project.has_git,
                    createdAt=int(project.created_at.timestamp() * 1000),
                    updatedAt=int(project.updated_at.timestamp() * 1000),
                )
                for project in self._repository.list_projects()
            ]
        )

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
