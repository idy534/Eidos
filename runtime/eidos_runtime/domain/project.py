from __future__ import annotations

from datetime import UTC, datetime
import hashlib

from pydantic import Field, field_validator, model_validator

from eidos_runtime.models import EidosFrozenStrictModel


class Project(EidosFrozenStrictModel):
    """A filesystem workspace with an optional Git capability."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=120)
    workspace_root: str = Field(min_length=1, max_length=4096)
    git_repository_root: str | None = Field(default=None, max_length=4096)
    git_common_dir: str | None = Field(default=None, max_length=4096)
    created_at: datetime
    updated_at: datetime

    @field_validator("workspace_root", "git_repository_root", "git_common_dir")
    @classmethod
    def validate_absolute_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if "\x00" in value or not value.startswith("/"):
            raise ValueError("project path must be absolute")
        return value

    @model_validator(mode="after")
    def validate_git_capability(self) -> "Project":
        if (self.git_repository_root is None) != (self.git_common_dir is None):
            raise ValueError(
                "git_repository_root and git_common_dir must be set together"
            )
        return self

    @property
    def has_git(self) -> bool:
        return self.git_repository_root is not None

    @field_validator("created_at", "updated_at")
    @classmethod
    def validate_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("project timestamp must be UTC")
        normalized = value.astimezone(UTC)
        if normalized.microsecond % 1_000:
            raise ValueError("project timestamp must use millisecond precision")
        return normalized


class DeletedProject(EidosFrozenStrictModel):
    """The durable result of removing one Project metadata record."""

    deleted_project_id: str = Field(min_length=1)


def direct_project_id(workspace_root: str) -> str:
    """Return the stable Project identity for a direct filesystem workspace."""

    return "project_" + hashlib.sha256(
        f"workspace\0{workspace_root}".encode("utf-8")
    ).hexdigest()


def default_project_name(workspace_root: str) -> str:
    """Return the stable display fallback for an unnamed legacy Project."""

    name = workspace_root.rstrip("/").rsplit("/", 1)[-1]
    return name or workspace_root


__all__ = [
    "DeletedProject",
    "Project",
    "default_project_name",
    "direct_project_id",
]
