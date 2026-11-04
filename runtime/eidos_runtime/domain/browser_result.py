"""Display projection for the bundled Playwright MCP (pinned 0.0.84) text results.

Playwright saves screenshots, snapshots and console logs under its private
``--output-dir``. That directory lives in the MCP sandbox and disappears with
the connection, so neither the model nor the user can open those links. The
projection replaces them with plain notes and extracts the page facts the
Desktop feed shows. Parsing is best effort: an unknown layout yields ``None``
and the feed falls back to the generic MCP view.
"""
from __future__ import annotations

import re

BUNDLED_BROWSER_TOOL_PREFIX = "mcp__browser__browser_"
MAX_VIEW_TEXT = 2000
MAX_VIEW_TABS = 20
MAX_VIEW_URL = 2048

_OUTPUT_LINK = re.compile(r"\[([^\]\n]*)\]\((?:[^()\s]*/)?browser-output/([^()\s#]+)(?:#[^()\s]*)?\)")
_OUTPUT_PATH = re.compile(r"(?:[^\s()'\"\[\]]*/)?browser-output/([^\s()'\"#]+)(?:#[^\s()'\"]*)?")
_SECTION = re.compile(r"^### (.+)$", re.M)
_TAB = re.compile(r"^- (\d+): (\(current\) )?\[(.*)\]\((.*)\)\s*$")
_CONSOLE = re.compile(r"^- Console: (\d+) errors?, (\d+) warnings?\s*$", re.M)


def _file_note(name: str) -> str:
    if name.endswith((".png", ".jpeg", ".jpg")):
        return "attached to this result"
    if name.endswith(".yml"):
        return "not retained; call browser_snapshot to read the page"
    if name.endswith(".log"):
        return "not retained; call browser_console_messages to read them"
    return "not retained"


def clean_browser_text(text: str) -> str:
    """Replace links into Playwright's private output directory with plain notes."""

    text = _OUTPUT_LINK.sub(lambda match: f"{match.group(1) or 'File'} ({_file_note(match.group(2))})", text)
    return _OUTPUT_PATH.sub(lambda match: f"({_file_note(match.group(1))})", text)


def _sections(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    matches = list(_SECTION.finditer(text))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        found.setdefault(match.group(1).strip(), text[match.end():end].strip("\n"))
    return found


def _bounded(value: str, limit: int = MAX_VIEW_TEXT) -> str:
    return value if len(value) <= limit else value[:limit] + "…"


def _fenced(body: str) -> str:
    lines = body.strip().splitlines()
    if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].startswith("```"):
        lines = lines[1:-1]
    return "\n".join(lines)


def browser_view(text: str) -> dict[str, object] | None:
    """Page facts the feed renders; ``None`` when the text has no known section."""

    sections = _sections(text)
    if not sections:
        return None
    view: dict[str, object] = {}
    page = sections.get("Page", sections.get("Page state"))
    if page is not None:
        facts: dict[str, str] = {}
        for line in page.splitlines():
            if line.startswith("- Page URL: "):
                facts["url"] = _bounded(line.removeprefix("- Page URL: ").strip(), MAX_VIEW_URL)
            elif line.startswith("- Page Title: "):
                facts["title"] = _bounded(line.removeprefix("- Page Title: ").strip(), 300)
        if facts:
            view["page"] = facts
        console = _CONSOLE.search(page)
        if console:
            view["console"] = {"errors": int(console.group(1)), "warnings": int(console.group(2))}
    tabs_text = sections.get("Open tabs")
    if tabs_text is not None:
        tabs: list[dict[str, object]] = []
        for line in tabs_text.splitlines():
            match = _TAB.match(line)
            if match and len(tabs) < MAX_VIEW_TABS:
                tabs.append({
                    "index": int(match.group(1)), "current": bool(match.group(2)),
                    "title": _bounded(match.group(3), 300), "url": _bounded(match.group(4), MAX_VIEW_URL),
                })
        if tabs:
            view["tabs"] = tabs
    result = sections.get("Result")
    if result:
        view["result"] = _bounded(clean_browser_text(result.strip()))
    snapshot = sections.get("Snapshot")
    if snapshot and snapshot.lstrip().startswith("```"):
        view["snapshotLines"] = len(_fenced(snapshot).splitlines())
    error = sections.get("Error")
    if error:
        view["error"] = _bounded(clean_browser_text(error.strip()))
    return view or None
