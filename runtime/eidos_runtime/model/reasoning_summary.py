"""Live reasoning text for the Feed's activity line, and nowhere else.

The sink travels in a ContextVar through the existing async portal so
``ModelClient.complete`` keeps its signature. Text is either a provider-written
summary (Responses ``reasoning.summary``) or raw reasoning content (Chat
Completions ``reasoning_content``, ``raw=True``). Both are presentation only:
never stored, never replayed, never part of model context.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

# (part_id, text so far, final, raw). The same id with longer text is an update.
ReasoningSummarySink = Callable[[str, str, bool, bool], None]

_sink: ContextVar[ReasoningSummarySink | None] = ContextVar(
    "eidos_reasoning_summary_sink", default=None
)


@contextmanager
def reasoning_summary_sink(sink: ReasoningSummarySink) -> Iterator[None]:
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)


def emit_reasoning_summary(summary_id: str, text: str, *, final: bool = False, raw: bool = False) -> None:
    sink = _sink.get()
    if sink is not None:
        sink(summary_id, text, final, raw)
