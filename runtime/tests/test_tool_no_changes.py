from __future__ import annotations

import json
from pathlib import Path
import sys
import threading

import pytest

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.runtime.engine import RuntimeEngine


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "darwin", reason="Shell uses the native macOS sandbox")
@pytest.mark.parametrize("approval_mode", ["manual", "auto_review", "full_access"])
@pytest.mark.parametrize("file_count", [1, 2])
@pytest.mark.parametrize("same_batch", [False, True])
def test_unchanged_patch_allows_following_shell_and_run_completion(
    tmp_path: Path, approval_mode: str, file_count: int, same_batch: bool,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    targets = [workspace / f"article-{index}.txt" for index in range(file_count)]
    for target in targets:
        target.write_text("Keep this text.\n")
    versions = [target.stat() for target in targets]
    patch = "*** Begin Patch\n" + "".join(
        f"*** Update File: {target.name}\n@@\n-Keep this text.\n+Keep this text.\n"
        for target in targets
    ) + "*** End Patch"
    noop = ModelToolCall("unchanged", "apply_patch", {"patch": patch})
    shell = ModelToolCall("following-shell", "run_shell", {
        "command": "cat article-0.txt", "cwd": ".",
    })
    responses = (
        [ModelResponse(tool_calls=(noop, shell))] if same_batch else
        [ModelResponse(tool_calls=(noop,)), ModelResponse(tool_calls=(shell,))]
    )
    model = ScriptedModel([*responses, ModelResponse(text="Done.")])
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        session = store.create_session(str(workspace))
        run, _ = store.create_run(session["id"], "Keep the existing text.", approval_mode=approval_mode)
        RuntimeEngine(store, model, lambda _: None, shell_available=True).run(run["id"], threading.Event())
        fact = store.read_run(run["id"])
        assert fact["status"] == "succeeded"
        tools = [item["toolCall"] for item in store.read_session_snapshot(session["id"])["items"]
                 if "toolCall" in item]
        unchanged, executed = [json.loads(tool["resultJson"]) for tool in tools]
        assert unchanged["code"] == "no_changes"
        assert unchanged["sideEffectsMayExist"] is False
        assert unchanged["reconciliationRequired"] is False
        assert unchanged["data"]["changes"] == []
        assert executed["outcome"] == "success", executed
        assert executed["data"]["exitCode"] == 0
        assert executed["data"]["stdout"] == "Keep this text.\n"
        assert store.connection.execute(
            "SELECT COUNT(*) FROM durable_intents WHERE tool_call_id=?", (tools[0]["id"],),
        ).fetchone()[0] == 0
        assert store.connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
        assert store.connection.execute(
            "SELECT COUNT(*) FROM events WHERE run_id=? AND event_type='reconciliation.required'",
            (run["id"],),
        ).fetchone()[0] == 0
        for target, version in zip(targets, versions, strict=True):
            assert target.read_text() == "Keep this text.\n"
            current = target.stat()
            assert (current.st_ino, current.st_mtime_ns) == (version.st_ino, version.st_mtime_ns)
    finally:
        store.close()


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "darwin", reason="Secure file commit uses macOS descriptor identity")
@pytest.mark.parametrize("operation", ["empty_add", "same_content_move", "mixed_update"])
def test_real_file_operations_still_commit_a_durable_intent(tmp_path: Path, operation: str) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "source.txt"
    source.write_text("old\n")
    unchanged = workspace / "unchanged.txt"
    unchanged.write_text("keep\n")
    body = {
        "empty_add": "*** Add File: empty.txt\n",
        "same_content_move": "*** Update File: source.txt\n*** Move to: moved.txt\n@@\n-old\n+old\n",
        "mixed_update": "*** Update File: unchanged.txt\n@@\n-keep\n+keep\n"
                        "*** Update File: source.txt\n@@\n-old\n+new\n",
    }[operation]
    model = ScriptedModel([
        ModelResponse(tool_calls=(ModelToolCall("change", "apply_patch", {
            "patch": f"*** Begin Patch\n{body}*** End Patch",
        }),)),
        ModelResponse(text="Done."),
    ])
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        session = store.create_session(str(workspace))
        run, _ = store.create_run(session["id"], "Apply the requested file change.")
        RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
        assert store.read_run(run["id"])["status"] == "succeeded"
        intent = store.connection.execute(
            "SELECT status FROM durable_intents WHERE run_id=?", (run["id"],),
        ).fetchall()
        assert [row[0] for row in intent] == ["completed"]
        if operation == "empty_add":
            assert (workspace / "empty.txt").read_bytes() == b""
        elif operation == "same_content_move":
            assert not source.exists()
            assert (workspace / "moved.txt").read_text() == "old\n"
        else:
            assert source.read_text() == "new\n"
            assert unchanged.read_text() == "keep\n"
    finally:
        store.close()
