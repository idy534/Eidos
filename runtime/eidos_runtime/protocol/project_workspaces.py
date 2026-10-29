from __future__ import annotations

from pydantic import Field

from eidos_runtime.models import EidosStrictModel


class WorkspaceDto(EidosStrictModel):
    def to_json_value(self) -> dict[str, object]:
        return self.to_wire_dict(exclude_none=True)


class ProjectRootDto(WorkspaceDto):
    id: str = Field(min_length=1, max_length=256)
    path: str = Field(min_length=1, max_length=4096)
    repository_root: str | None = None
    shared: bool = False


class ProjectRootInput(WorkspaceDto):
    id: str = Field(min_length=1, max_length=256)
    path: str = Field(min_length=1, max_length=4096)


class ProjectReadRequest(WorkspaceDto):
    project_id: str = Field(min_length=1, max_length=256)


class ProjectCreateFoldersRequest(WorkspaceDto):
    operation_id: str = Field(min_length=1, max_length=256)
    name: str | None = Field(default=None, max_length=120)
    roots: list[ProjectRootInput] = Field(min_length=1, max_length=64)
    primary_root_id: str = Field(min_length=1, max_length=256)


class ProjectUpdateRequest(ProjectReadRequest):
    operation_id: str = Field(min_length=1, max_length=256)
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, max_length=120)
    roots: list[ProjectRootInput] = Field(min_length=1, max_length=64)
    primary_root_id: str = Field(min_length=1, max_length=256)

