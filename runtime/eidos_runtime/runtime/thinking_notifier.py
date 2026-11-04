"""Publishes bounded, non-durable thinking summaries for the live Feed line.

A summary is presentation only: it is never stored, never enters model context
and is not replayed after a restart. A lost notification costs nothing.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Callable

from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.sandbox.sensitive import SensitiveScanner


logger = logging.getLogger("eidos.runtime")

MAX_SUMMARY_SOURCE_CHARS = 4096
MAX_TITLE_CHARS = 80
MAX_BODY_CHARS = 600
MIN_PUBLISH_INTERVAL_SECONDS = 0.25
_BOLD_TITLE = re.compile(r"^\s*\*\*(.+?)\*\*", re.DOTALL)


def split_summary(text: str, *, final: bool) -> tuple[str, str] | None:
    """Return (title, body), or None while the title is still being written."""
    text = text.strip()
    if not text:
        return None
    bold = _BOLD_TITLE.match(text)
    if bold is not None:
        title, rest = bold.group(1), text[bold.end():]
    elif text.startswith("**"):
        if not final:
            return None
        title, rest = text.lstrip("*"), ""
    else:
        first, newline, rest = text.partition("\n")
        if not newline and not final:
            return None
        title = first
    title = " ".join(title.replace("#", " ").replace("*", " ").split())
    if not title:
        return None
    if len(title) > MAX_TITLE_CHARS:
        title = title[:MAX_TITLE_CHARS - 1].rstrip() + "…"
    body = rest.strip()
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS - 1].rstrip() + "…"
    return title, body


class ThinkingSummaryNotifier:
    """One per model attempt; called from the streaming worker thread."""

    def __init__(
        self,
        events: RuntimeEvents,
        sensitive: SensitiveScanner,
        session_id: str,
        run_id: str,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._events = events
        self._sensitive = sensitive
        self._session_id = session_id
        self._run_id = run_id
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._last_published: float | None = None
        self._last_params: tuple[str, str, str] | None = None

    def receive(self, summary_id: str, text: str, final: bool) -> None:
        try:
            self._receive(summary_id, text[:MAX_SUMMARY_SOURCE_CHARS], final)
        except Exception:
            # Presentation only: a failure here must never affect the model request.
            logger.warning("Thinking summary was not published", exc_info=True)

    def _receive(self, summary_id: str, text: str, final: bool) -> None:
        # Decide on throttling before any parsing or redaction: deltas arrive far
        # faster than notifications are published.
        with self._lock:
            now = self._monotonic()
            last = self._last_params
            if (
                not final
                and last is not None
                and last[0] == summary_id
                and self._last_published is not None
                and now - self._last_published < MIN_PUBLISH_INTERVAL_SECONDS
            ):
                return
        parts = split_summary(text, final=final)
        if parts is None:
            return
        title, body = parts
        title = self._sensitive.redact_for_presentation(title).text
        body = self._sensitive.redact_for_presentation(body).text if body else ""
        key = (summary_id, title, body)
        with self._lock:
            if key == self._last_params:
                return
            self._last_published = now
            self._last_params = key
        self._events.publish_transient("run/thinking", {
            "sessionId": self._session_id,
            "runId": self._run_id,
            "summaryId": summary_id,
            "title": title,
            **({"body": body} if body else {}),
        })
