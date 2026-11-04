"""Provider-written reasoning summaries, handed to the live Feed and nowhere else.

The sink travels in a ContextVar through the existing async portal so
``ModelClient.complete`` keeps its signature. Only summary-grade text from a
provider's explicit summary stream may be emitted; raw reasoning content
(for example Chat Completions ``reasoning_content``) must not use this path.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

# (summary_id, text so far, final). The same id with longer text is an update.
ReasoningSummarySink = Callable[[str, str, bool], None]

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


def emit_reasoning_summary(summary_id: str, text: str, *, final: bool = False) -> None:
    sink = _sink.get()
    if sink is not None:
        sink(summary_id, text, final)
