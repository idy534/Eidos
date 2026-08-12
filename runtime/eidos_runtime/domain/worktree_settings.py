from __future__ import annotations

from datetime import UTC, datetime

from pydantic import Field, field_validator

from eidos_runtime.models.base import EidosFrozenStrictModel


class WorktreeSettings(EidosFrozenStrictModel):
    """The two retention settings owned by the Worktree feature."""

    automatic_cleanup: bool = Field(alias="automaticCleanup")
    managed_worktree_limit: int = Field(
        alias="managedWorktreeLimit", ge=1, le=100
    )
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def validate_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("settings timestamp must be UTC")
        normalized = value.astimezone(UTC)
        if normalized.microsecond % 1_000:
            raise ValueError("settings timestamp must use millisecond precision")
        return normalized


__all__ = ["WorktreeSettings"]
