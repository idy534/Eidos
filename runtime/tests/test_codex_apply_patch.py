from __future__ import annotations

from pathlib import Path
import threading
from unittest.mock import patch

from eidos_runtime.tools.workspace import TOOL_SPECS, ToolExecutor


def _run_patch(executor: ToolExecutor, patch_text: str):
    prepared = executor.prepare_file_change(
        "apply_patch", {"patch": patch_text}, threading.Event()
    )
    assert not isinstance(prepared, dict), prepared
    return executor.commit_patch("apply_patch", prepared, threading.Event())


def _executor(tmp_path: Path) -> ToolExecutor:
    return ToolExecutor(tmp_path)


def test_model_catalog_exposes_only_apply_patch_for_file_mutation() -> None:
    names = {spec.name for spec in TOOL_SPECS}
    assert "apply_patch" in names
    assert "write_file" not in names
    assert "delete_file" not in names
    spec = next(spec for spec in TOOL_SPECS if spec.name == "apply_patch")
    assert set(spec.input_schema["properties"]) == {"patch"}
    assert spec.input_schema["required"] == ["patch"]


def test_add_file_creates_missing_parent_directories(tmp_path: Path) -> None:
    with _executor(tmp_path) as executor:
        result, delta = _run_patch(
            executor,
            "*** Begin Patch\n*** Add File: src/core/types.ts\n+export type ID = string;\n*** End Patch",
        )

    assert result["outcome"] == "success"
    assert (tmp_path / "src/core/types.ts").read_text() == "export type ID = string;\n"
    assert delta.changes[0].kind == "add"
    assert delta.changes[0].path == "src/core/types.ts"


def test_update_supports_bare_context_and_context_header(tmp_path: Path) -> None:
    target = tmp_path / "events.ts"
    target.write_text("first\nfunction handle()\nold\nlast\n")
    with _executor(tmp_path) as executor:
        result, _ = _run_patch(
            executor,
            "*** Begin Patch\n*** Update File: events.ts\n@@\n-first\n+start\n@@ function handle()\n-old\n+new\n*** End Patch",
        )

    assert result["outcome"] == "success"
    assert target.read_text() == "start\nfunction handle()\nnew\nlast\n"


def test_update_supports_multiple_chunks_and_end_of_file(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("one\ntwo\nthree\n")
    with _executor(tmp_path) as executor:
        result, delta = _run_patch(
            executor,
            "*** Begin Patch\n*** Update File: app.py\n@@\n-one\n+ONE\n@@\n-three\n+THREE\n*** End of File\n*** End Patch",
        )

    assert result["outcome"] == "success"
    assert target.read_text() == "ONE\ntwo\nTHREE\n"
    assert delta.changes[0].old_content == "one\ntwo\nthree\n"
    assert delta.changes[0].new_content == "ONE\ntwo\nTHREE\n"


def test_delete_and_move_are_file_changes(tmp_path: Path) -> None:
    (tmp_path / "old.txt").write_text("old\n")
    with _executor(tmp_path) as executor:
        result, delta = _run_patch(
            executor,
            "*** Begin Patch\n*** Update File: old.txt\n*** Move to: nested/new.txt\n@@\n-old\n+moved\n*** Delete File: nested/new.txt\n*** End Patch",
        )

    assert result["outcome"] == "success"
    assert not (tmp_path / "old.txt").exists()
    assert not (tmp_path / "nested/new.txt").exists()
    assert [change.kind for change in delta.changes] == ["move", "delete"]


def test_delete_file_is_reported_as_a_committed_file_change(tmp_path: Path) -> None:
    (tmp_path / "obsolete.txt").write_text("remove me\n")
    with _executor(tmp_path) as executor:
        result, delta = _run_patch(
            executor,
            "*** Begin Patch\n*** Delete File: obsolete.txt\n*** End Patch",
        )

    assert result["outcome"] == "success"
    assert not (tmp_path / "obsolete.txt").exists()
    assert delta.changes[0].kind == "delete"


def test_patch_can_change_multiple_files_and_reports_summary(tmp_path: Path) -> None:
    (tmp_path / "existing.txt").write_text("old\n")
    with _executor(tmp_path) as executor:
        result, delta = _run_patch(
            executor,
            "*** Begin Patch\n*** Add File: added.txt\n+new\n*** Update File: existing.txt\n@@\n-old\n+updated\n*** End Patch",
        )

    assert result["outcome"] == "success"
    assert "A added.txt" in result["summary"]
    assert "M existing.txt" in result["summary"]
    assert [change.path for change in delta.changes] == ["added.txt", "existing.txt"]


def test_context_mismatch_and_malformed_patch_are_actionable(tmp_path: Path) -> None:
    (tmp_path / "events.ts").write_text("actual\n")
    with _executor(tmp_path) as executor:
        mismatch = executor.prepare_file_change(
            "apply_patch",
            {
                "patch": "*** Begin Patch\n*** Update File: events.ts\n@@ missing context\n-old\n+new\n*** End Patch"
            },
            threading.Event(),
        )
        malformed = executor.prepare_file_change(
            "apply_patch",
            {"patch": "*** Begin Patch\n*** Update File: events.ts\n-old\n+new\n*** End Patch"},
            threading.Event(),
        )

    assert mismatch["code"] == "patch_context_mismatch"
    assert "events.ts" in mismatch["summary"]
    assert "missing context" in mismatch["summary"]
    assert malformed["code"] == "patch_format_error"
    assert "hunk" in malformed["summary"].lower()
    assert "line 3" in malformed["summary"]
    assert "events.ts" in malformed["summary"]


def test_workspace_boundary_remains_fail_closed(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    with _executor(tmp_path) as executor:
        prepared = executor.prepare_file_change(
            "apply_patch",
            {"patch": "*** Begin Patch\n*** Add File: ../outside.txt\n+secret\n*** End Patch"},
            threading.Event(),
        )
        if isinstance(prepared, dict):
            result = prepared
        else:
            result, _ = executor.commit_patch("apply_patch", prepared, threading.Event())
    assert result["outcome"] == "error"
    assert result["code"] == "workspace_boundary_violation"
    assert not outside.exists()


def test_failed_later_commit_keeps_committed_prefix(tmp_path: Path) -> None:
    (tmp_path / "first.txt").write_text("one\n")
    (tmp_path / "second.txt").write_text("two\n")
    with _executor(tmp_path) as executor:
        prepared = executor.prepare_file_change(
            "apply_patch",
            {
                "patch": "*** Begin Patch\n*** Update File: first.txt\n@@\n-one\n+ONE\n*** Update File: second.txt\n@@\n-two\n+TWO\n*** End Patch"
            },
            threading.Event(),
        )
        assert not isinstance(prepared, dict)
        real_commit = executor.commit_file_change
        calls = 0

        def fail_second(tool_name, change, cancel):
            nonlocal calls
            calls += 1
            if calls == 2:
                return {
                    "outcome": "error",
                    "code": "file_version_conflict",
                    "summary": "File changed before commit",
                    "data": {"path": change.path},
                    "sideEffectsMayExist": False,
                    "reconciliationRequired": False,
                }
            return real_commit(tool_name, change, cancel)

        with patch.object(executor, "commit_file_change", side_effect=fail_second):
                result, delta = executor.commit_patch("apply_patch", prepared, threading.Event())

    assert result["outcome"] == "error"
    assert result["code"] == "file_version_conflict"
    assert result["data"]["changes"][0]["path"] == "first.txt"
    assert [change.path for change in delta.changes] == ["first.txt"]
    assert (tmp_path / "first.txt").read_text() == "ONE\n"
    assert (tmp_path / "second.txt").read_text() == "two\n"
