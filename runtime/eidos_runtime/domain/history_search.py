"""Matches from a Session's saved history. They point at originals, never replace them."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from eidos_runtime.models import EidosFrozenStrictModel


class HistoryMatch(EidosFrozenStrictModel):
    item_id: str = Field(min_length=1)
    kind: Literal["user", "assistant", "tool"]
    snippet: str
    call_id: str | None = None
    tool_name: str | None = None


class HistorySearchResult(EidosFrozenStrictModel):
    matches: tuple[HistoryMatch, ...]
    # False when more matches exist, part of the message index is still being
    # built, or the tool-call scan stopped at its bound.
    complete: bool
