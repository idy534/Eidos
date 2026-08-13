from __future__ import annotations

from typing import Literal

from eidos_runtime.models import EidosFrozenStrictModel


class ReviewComment(EidosFrozenStrictModel):
    id: str
    session_id: str
    path: str
    scope: Literal["head", "baseline"]
    side: Literal["old", "new"]
    line: int
    body: str
    base_head: str
    diff_hash: str
    status: Literal["active", "stale"]
    created_at: int
    updated_at: int


__all__ = ["ReviewComment"]
