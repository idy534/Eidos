from __future__ import annotations

from pathlib import PurePosixPath

from pydantic import Field, field_validator

from eidos_runtime.models import EidosFrozenStrictModel


class ProjectRoot(EidosFrozenStrictModel):
    id: str = Field(min_length=1, max_length=256)
    path: str = Field(min_length=1, max_length=4096)
    repository_root: str | None = None
    git_dir: str | None = None
    git_common_dir: str | None = None

    @field_validator("path", "repository_root", "git_dir", "git_common_dir")
    @classmethod
    def absolute_path(cls, value: str | None) -> str | None:
        if value is not None and (
            not value.startswith("/") or "\x00" in value
            or ".." in PurePosixPath(value).parts
        ):
            raise ValueError("workspace paths must be absolute")
        return value


class DirectoryIdentity(EidosFrozenStrictModel):
    device: int = Field(ge=0)
    inode: int = Field(ge=0)
    owner: int = Field(ge=0)


class SessionRoot(ProjectRoot):
    git_dir_identity: DirectoryIdentity | None = None
    git_common_identity: DirectoryIdentity | None = None
    device: int = Field(ge=0)
    inode: int = Field(ge=0)
    owner: int = Field(ge=0)

