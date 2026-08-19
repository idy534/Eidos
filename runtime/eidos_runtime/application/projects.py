from __future__ import annotations

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
from eidos_runtime.persistence.worktrees import ProjectWorktreeRepository
from eidos_runtime.protocol.methods import (
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
    ) -> None:
        self._repository = repository
        self._lifecycle = lifecycle or SessionLifecycleCoordinator()

    def list(self, _request: ProjectListRequestDto) -> ProjectListResponseDto:
        return ProjectListResponseDto(
            items=[
                ProjectDto(
                    id=project.id,
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
