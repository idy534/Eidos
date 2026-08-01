from __future__ import annotations

from pydantic import Field

from eidos_runtime.models import EidosFrozenStrictModel, JsonSafeInt


class Checkpoint(EidosFrozenStrictModel):
    id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    item_ordinal: int = Field(ge=0)
    rule_snapshot_id: str | None = None
    repository_snapshot_id: str | None = None
    context_snapshot_id: str | None = None
    compact_summary_id: str | None = None
    workspace_identity_hash: str = Field(min_length=1)
    git_head: str | None = None
    permission_snapshot_hash: str | None = None
    model_profile_snapshot_hash: str = Field(min_length=1)
    reconciliation_required: bool
    created_at: JsonSafeInt


__all__ = ["Checkpoint"]
