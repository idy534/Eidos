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
    # Set for matches from another Session of the same project.
    session_id: str | None = None
    session_title: str | None = None
    date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class HistoryScopeError(ValueError):
    """A stable reason why other Sessions cannot be searched or read."""

    def __init__(self, code: Literal["history_scope_disabled", "history_scope_unavailable"]) -> None:
        super().__init__(code)
        self.code = code


class HistorySearchResult(EidosFrozenStrictModel):
    matches: tuple[HistoryMatch, ...]
    # False when more matches exist, part of the message index is still being
    # built, or the tool-call scan stopped at its bound.
    complete: bool
