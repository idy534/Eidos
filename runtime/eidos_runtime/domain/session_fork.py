from __future__ import annotations

from typing import Literal

from pydantic import Field

from eidos_runtime.models import EidosFrozenStrictModel, JSON_SAFE_INTEGER_MAX


class SessionForkOrigin(EidosFrozenStrictModel):
    session_id: str = Field(min_length=1, max_length=256)
    run_id: str = Field(min_length=1, max_length=256)
    item_id: str = Field(min_length=1, max_length=256)


class SessionForkSource(SessionForkOrigin):
    item_count: int = Field(ge=1, le=JSON_SAFE_INTEGER_MAX)
    created_at: int = Field(ge=0, le=JSON_SAFE_INTEGER_MAX)


class SameWorkspaceForkTarget(EidosFrozenStrictModel):
    kind: Literal["same_workspace"]


class NewWorktreeForkTarget(EidosFrozenStrictModel):
    kind: Literal["new_worktree"]
    state: Literal["current_workspace"] = "current_workspace"
