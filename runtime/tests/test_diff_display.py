from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from unidiff import PatchSet

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.file_limits import INLINE_TOOL_TEXT_BYTES
from eidos_runtime.protocol.tool_text import project_tool_text
from eidos_runtime.runtime.event_projector import EventProjector
from eidos_runtime.tools.workspace import _build_change_diff  # noqa: PLC2701


FIXTURES = json.loads(
    (Path(__file__).parents[2] / "protocol/fixtures/text-diffs-v1.json").read_text()
)


@pytest.mark.parametrize("case", FIXTURES, ids=lambda case: case["name"])
def test_diff_display_matches_desktop_fixture_without_changing_evidence(case) -> None:
    raw = case["rawDiff"]
    item = {
        "id": "item-1", "sessionId": "session-1", "runId": "run-1",
        "kind": "file_change", "status": "completed",
        "toolCall": {"id": "tool-1", "changeDiff": raw},
    }

    projected = project_tool_text(item)

    assert projected["toolCall"]["changeDiff"] == case["displayDiff"]
    assert item["toolCall"]["changeDiff"] == raw
    assert project_tool_text(projected) == projected
    notification, = EventProjector().project({"eventType": "item.completed"}, item=item)
    assert notification["params"]["item"]["toolCall"]["changeDiff"] == case["displayDiff"]
    assert [(f.added, f.removed) for f in PatchSet(case["displayDiff"])] == [
        (f.added, f.removed) for f in PatchSet(raw)
    ]


@pytest.mark.parametrize("original,candidate,fromfile,tofile,case_name", [
    ("", "one\ntwo\nthree\n", "/dev/null", "b/build-sea.js", "created"),
    ("old\nsame\n", "new\nextra\nsame\n", "a/src/main.ts", "b/src/main.ts", "modified"),
    ("old\ngone\n", "", "a/gone.txt", "/dev/null", "deleted"),
    ("old\n", "new\n", "a/old.ts", "b/new.ts", "renamed"),
    ("old\n", "new\n", "a/docs/海洋 notes.md", "b/docs/海洋 notes.md", "space-unicode"),
    ("same", "same\n", "a/notes.txt", "b/notes.txt", "eof-only"),
    ("same\r\n", "same\n", "a/notes.txt", "b/notes.txt", "line-endings-only"),
])
def test_fixture_uses_the_real_workspace_diff_format(
    original: str, candidate: str, fromfile: str, tofile: str, case_name: str,
) -> None:
    case = next(case for case in FIXTURES if case["name"] == case_name)
    raw = _build_change_diff(original, candidate, fromfile, tofile)
    assert raw == case["rawDiff"]


@pytest.mark.parametrize("raw", [
    "", "not a patch", "--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n-old\n+new\n",
    "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n",
])
def test_empty_invalid_or_git_diff_keeps_its_original_display(raw: str) -> None:
    assert project_tool_text({"toolCall": {"changeDiff": raw}})["toolCall"]["changeDiff"] == raw


def test_large_diff_keeps_the_original_paging_hash_and_bytes() -> None:
    raw = "--- /dev/null\n+++ b/x\n@@ -0,0 +1 @@\n+" + "x" * INLINE_TOOL_TEXT_BYTES + "\n"
    projected = project_tool_text({"toolCall": {"changeDiff": raw}})["toolCall"]
    assert projected["changeDiff"] == ""
    assert projected["changeDiffBytes"] == len(raw.encode())
    assert projected["changeDiffHash"] == hashlib.sha256(raw.encode()).hexdigest()


def test_display_header_overhead_uses_original_text_for_paging() -> None:
    prefix = "--- /dev/null\n+++ b/x\n@@ -0,0 +1 @@\n+"
    raw = prefix + "x" * (INLINE_TOOL_TEXT_BYTES - len(prefix) - 1) + "\n"
    assert len(raw.encode()) == INLINE_TOOL_TEXT_BYTES
    projected = project_tool_text({"toolCall": {"changeDiff": raw}})["toolCall"]
    assert projected["changeDiff"] == ""
    assert projected["changeDiffHash"] == hashlib.sha256(raw.encode()).hexdigest()


@pytest.mark.integration
def test_existing_patch_is_projected_after_reopen_without_rewriting_sqlite(tmp_path: Path) -> None:
    data = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data.mkdir(mode=0o700)
    workspace.mkdir()
    case = next(case for case in FIXTURES if case["name"] == "multiple-files")
    raw = case["rawDiff"]
    store = SessionStore(data)
    store.initialize()
    try:
        session = store.create_session(str(workspace))
        run, _ = store.create_run(session["id"], "Edit files")
        item = store.create_tool_item(run["id"], 1, 0, "call-1", "apply_patch", "{}")
        tool_id = item["toolCall"]["id"]
        change = store.record_workspace_change(item["id"], diff=raw, base_sha256=None)
        notification, = EventProjector().project(change.events[-1], item=change.value)
        assert notification["params"]["item"]["toolCall"]["changeDiff"] == case["displayDiff"]
    finally:
        store.close()

    reopened = SessionStore(data)
    reopened.initialize()
    try:
        snapshot = reopened.read_session_snapshot(session["id"])
        tool_item = next(item for item in snapshot["items"] if item.get("toolCall"))
        assert tool_item["toolCall"]["changeDiff"] == case["displayDiff"]
        assert reopened.connection.execute(
            "SELECT approval_diff FROM tool_calls WHERE id = ?", (tool_id,),
        ).fetchone()[0] == raw
        page = reopened.read_tool_text(
            session["id"], tool_id, "diff", hashlib.sha256(raw.encode()).hexdigest(), 0,
        )
        assert page.content == raw
    finally:
        reopened.close()
