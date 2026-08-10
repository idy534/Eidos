from __future__ import annotations

from datetime import UTC, datetime

from pydantic import Field, field_validator

from eidos_runtime.models import EidosFrozenStrictModel


class Project(EidosFrozenStrictModel):
    """A verified Git repository used as the identity of a Project."""

    id: str = Field(min_length=1)
    repository_root: str = Field(min_length=1, max_length=4096)
    git_common_dir: str = Field(min_length=1, max_length=4096)
    created_at: datetime
    updated_at: datetime

    @field_validator("repository_root", "git_common_dir")
    @classmethod
    def validate_absolute_path(cls, value: str) -> str:
        if "\x00" in value or not value.startswith("/"):
            raise ValueError("project path must be absolute")
        return value

    @field_validator("created_at", "updated_at")
    @classmethod
    def validate_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("project timestamp must be UTC")
        normalized = value.astimezone(UTC)
        if normalized.microsecond % 1_000:
            raise ValueError("project timestamp must use millisecond precision")
        return normalized


__all__ = ["Project"]
