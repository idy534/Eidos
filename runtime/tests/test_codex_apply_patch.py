from __future__ import annotations

from pathlib import Path
import threading
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from eidos_runtime.tools.contracts import ApplyPatchInput
from eidos_runtime.tools.workspace import TOOL_SPECS, ToolExecutor


def _run_patch(executor: ToolExecutor, arguments: dict[str, object]):
    prepared = executor.prepare_file_change(
        "apply_patch", arguments, threading.Event()
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


def test_apply_patch_contract_requires_non_empty_text() -> None:
    for value in ({"patch": ""}, {"patch": False}, {"patch": []}, {"changes": []}):
        with pytest.raises(ValidationError):
            ApplyPatchInput.model_validate(value)


def test_add_file_creates_missing_parent_directories(tmp_path: Path) -> None:
    with _executor(tmp_path) as executor:
        result, delta = _run_patch(
            executor,
            {"patch": (
                "*** Begin Patch\n"
                "*** Add File: src/core/types.ts\n"
                "+export type ID = string;\n"
                "*** End Patch\n"
            )},
        )

    assert result["outcome"] == "success"
    assert (tmp_path / "src/core/types.ts").read_text() == "export type ID = string;\n"
    assert delta.changes[0].kind == "add"
    assert delta.changes[0].path == "src/core/types.ts"


def test_function_apply_patch_accepts_workspace_absolute_path(tmp_path: Path) -> None:
    target = tmp_path / "src" / "main.py"
    with _executor(tmp_path) as executor:
        result, delta = _run_patch(
            executor,
            {"patch": (
                f"*** Begin Patch\n*** Add File: {target}\n"
                "+print('ok')\n*** End Patch\n"
            )},
        )

    assert result["outcome"] == "success"
    assert target.read_text() == "print('ok')\n"
    assert delta.changes[0].path == "src/main.py"
    assert result["data"]["changes"][0]["path"] == "src/main.py"


def test_custom_apply_patch_accepts_workspace_absolute_move_paths(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "nested" / "destination.txt"
    source.write_text("old\n")
    patch = (
        "*** Begin Patch\n"
        f"*** Update File: {source}\n"
        f"*** Move to: {destination}\n"
        "@@\n"
        "-old\n"
        "+new\n"
        "*** End Patch\n"
    )
    with ToolExecutor(
        tmp_path,
        supports_custom_tools=True,
        supports_tool_grammar=True,
    ) as executor:
        prepared = executor.prepare_file_change(
            "apply_patch", patch, threading.Event()
        )
        assert not isinstance(prepared, dict), prepared
        result, delta = executor.commit_patch(
            "apply_patch", prepared, threading.Event()
        )

    assert result["outcome"] == "success"
    assert not source.exists()
    assert destination.read_text() == "new\n"
    assert delta.changes[0].old_path == "source.txt"
    assert delta.changes[0].new_path == "nested/destination.txt"


@pytest.mark.parametrize("custom", (False, True))
def test_apply_patch_rejects_absolute_path_outside_workspace(
    tmp_path: Path, custom: bool
) -> None:
    outside = tmp_path.parent / f"eidos-outside-{tmp_path.name}.txt"
    if custom:
        arguments: object = (
            "*** Begin Patch\n"
            f"*** Add File: {outside}\n"
            "+outside\n"
            "*** End Patch\n"
        )
        executor = ToolExecutor(
            tmp_path,
            supports_custom_tools=True,
            supports_tool_grammar=True,
        )
    else:
        arguments = {"patch": (
            f"*** Begin Patch\n*** Add File: {outside}\n+outside\n*** End Patch\n"
        )}
        executor = _executor(tmp_path)
    with executor:
        prepared = executor.prepare_file_change(
            "apply_patch", arguments, threading.Event()
        )

    assert isinstance(prepared, dict)
    assert prepared["outcome"] == "error"
    assert prepared["code"] == "workspace_boundary_violation"
    assert not outside.exists()


def test_custom_apply_patch_rejects_absolute_move_destination_outside_workspace(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.txt"
    outside = tmp_path.parent / f"eidos-outside-move-{tmp_path.name}.txt"
    source.write_text("old\n")
    patch = (
        "*** Begin Patch\n"
        f"*** Update File: {source}\n"
        f"*** Move to: {outside}\n"
        "@@\n"
        "-old\n"
        "+new\n"
        "*** End Patch\n"
    )
    with ToolExecutor(
        tmp_path,
        supports_custom_tools=True,
        supports_tool_grammar=True,
    ) as executor:
        prepared = executor.prepare_file_change(
            "apply_patch", patch, threading.Event()
        )

    assert isinstance(prepared, dict)
    assert prepared["code"] == "workspace_boundary_violation"
    assert source.read_text() == "old\n"
    assert not outside.exists()


@pytest.mark.parametrize(
    ("body", "expected_bytes"),
    (("", b""), ("+\n", b"\n"), ("+\n+\n", b"\n\n")),
)
def test_add_file_preserves_empty_and_newline_bytes(
    tmp_path: Path, body: str, expected_bytes: bytes
) -> None:
    with _executor(tmp_path) as executor:
        result, _ = _run_patch(
            executor,
            {"patch": f"*** Begin Patch\n*** Add File: new.txt\n{body}*** End Patch\n"},
        )

    assert result["outcome"] == "success"
    assert (tmp_path / "new.txt").read_bytes() == expected_bytes


def test_add_file_preserves_trailing_space_in_path(tmp_path: Path) -> None:
    with _executor(tmp_path) as executor:
        result, _ = _run_patch(
            executor,
            {"patch": (
                "*** Begin Patch\n"
                "*** Add File: trailing.txt \n"
                "+x\n"
                "*** End Patch\n"
            )},
        )

    assert result["outcome"] == "success"
    assert (tmp_path / "trailing.txt ").read_bytes() == b"x\n"
    assert not (tmp_path / "trailing.txt").exists()


def test_update_supports_bare_context_and_context_header(tmp_path: Path) -> None:
    target = tmp_path / "events.ts"
    target.write_text("first\nfunction handle()\nold\nlast\n")
    with _executor(tmp_path) as executor:
        result, _ = _run_patch(
            executor,
            {"patch": (
                "*** Begin Patch\n"
                "*** Update File: events.ts\n"
                "@@\n"
                "-first\n"
                "+start\n"
                "@@ function handle()\n"
                "-old\n"
                "+new\n"
                "*** End Patch\n"
            )},
        )

    assert result["outcome"] == "success"
    assert target.read_text() == "start\nfunction handle()\nnew\nlast\n"


def test_update_supports_multiple_chunks_and_end_of_file(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("one\ntwo\nthree\n")
    with _executor(tmp_path) as executor:
        result, delta = _run_patch(
            executor,
            {"patch": (
                "*** Begin Patch\n"
                "*** Update File: app.py\n"
                "@@\n"
                "-one\n"
                "+ONE\n"
                "@@\n"
                "-three\n"
                "+THREE\n"
                "*** End of File\n"
                "*** End Patch\n"
            )},
        )

    assert result["outcome"] == "success"
    assert target.read_text() == "ONE\ntwo\nTHREE\n"
    assert delta.changes[0].old_content == "one\ntwo\nthree\n"
    assert delta.changes[0].new_content == "ONE\ntwo\nTHREE\n"


def test_update_supports_pure_insertion_and_deletion(tmp_path: Path) -> None:
    (tmp_path / "insert.txt").write_bytes(b"head\n")
    (tmp_path / "delete.txt").write_bytes(b"keep\nremove\n")
    with _executor(tmp_path) as executor:
        result, _ = _run_patch(
            executor,
            {"patch": (
                "*** Begin Patch\n"
                "*** Update File: insert.txt\n"
                "@@\n"
                "+tail\n"
                "*** Update File: delete.txt\n"
                "@@\n"
                "-remove\n"
                "*** End Patch\n"
            )},
        )

    assert result["outcome"] == "success"
    assert (tmp_path / "insert.txt").read_bytes() == b"head\ntail\n"
    assert (tmp_path / "delete.txt").read_bytes() == b"keep\n"


def test_update_preserves_file_without_final_newline(tmp_path: Path) -> None:
    target = tmp_path / "no-final-newline.txt"
    target.write_bytes(b"old")
    with _executor(tmp_path) as executor:
        result, _ = _run_patch(
            executor,
            {"patch": (
                "*** Begin Patch\n"
                "*** Update File: no-final-newline.txt\n"
                "@@\n"
                "-old\n"
                "+new\n"
                "*** End Patch\n"
            )},
        )

    assert result["outcome"] == "success"
    assert target.read_bytes() == b"new"


def test_apply_patch_rejects_canonical_patch_over_512_kib(tmp_path: Path) -> None:
    content = "x" * (256 * 1024)
    with _executor(tmp_path) as executor:
        prepared = executor.prepare_file_change(
            "apply_patch",
            {"patch": (
                f"*** Begin Patch\n*** Add File: first.txt\n+{content}\n"
                f"*** Add File: second.txt\n+{content}\n*** End Patch\n"
            )},
            threading.Event(),
        )

    assert isinstance(prepared, dict)
    assert prepared["outcome"] == "error"
    assert prepared["code"] == "patch_too_large"
    assert not (tmp_path / "first.txt").exists()
    assert not (tmp_path / "second.txt").exists()


def test_delete_and_move_are_file_changes(tmp_path: Path) -> None:
    (tmp_path / "old.txt").write_text("old\n")
    with _executor(tmp_path) as executor:
        result, delta = _run_patch(
            executor,
            {"patch": (
                "*** Begin Patch\n"
                "*** Update File: old.txt\n"
                "*** Move to: nested/new.txt\n"
                "@@\n"
                "-old\n"
                "+moved\n"
                "*** Delete File: nested/new.txt\n"
                "*** End Patch\n"
            )},
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
            {"patch": (
                "*** Begin Patch\n"
                "*** Delete File: obsolete.txt\n"
                "*** End Patch\n"
            )},
        )

    assert result["outcome"] == "success"
    assert not (tmp_path / "obsolete.txt").exists()
    assert delta.changes[0].kind == "delete"


def test_patch_can_change_multiple_files_and_reports_summary(tmp_path: Path) -> None:
    (tmp_path / "existing.txt").write_text("old\n")
    with _executor(tmp_path) as executor:
        result, delta = _run_patch(
            executor,
            {"patch": (
                "*** Begin Patch\n"
                "*** Add File: added.txt\n"
                "+new\n"
                "*** Update File: existing.txt\n"
                "@@\n"
                "-old\n"
                "+updated\n"
                "*** End Patch\n"
            )},
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
            {"patch": (
                "*** Begin Patch\n"
                "*** Update File: events.ts\n"
                "@@ missing context\n"
                "-old\n"
                "+new\n"
                "*** End Patch\n"
            )},
            threading.Event(),
        )
        malformed = executor.prepare_file_change(
            "apply_patch",
            {"patch": "*** Begin Patch\n*** Add File: new.txt\nmissing prefix\n*** End Patch\n"},
            threading.Event(),
        )

    assert mismatch["code"] == "patch_context_mismatch"
    assert "events.ts" in mismatch["summary"]
    assert "missing context" in mismatch["summary"]
    assert "Read the current target lines" in mismatch["summary"]
    assert malformed["code"] == "patch_format_error"
    assert "Correct the Patch markers" in malformed["summary"]
    assert "No files were changed" in malformed["summary"]
    assert not (tmp_path / "new.txt").exists()
    assert (tmp_path / "events.ts").read_text() == "actual\n"


def test_workspace_boundary_remains_fail_closed(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    with _executor(tmp_path) as executor:
        prepared = executor.prepare_file_change(
            "apply_patch",
            {"patch": (
                "*** Begin Patch\n"
                "*** Add File: ../outside.txt\n"
                "+secret\n"
                "*** End Patch\n"
            )},
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
            {"patch": (
                "*** Begin Patch\n"
                "*** Update File: first.txt\n"
                "@@\n"
                "-one\n"
                "+ONE\n"
                "*** Update File: second.txt\n"
                "@@\n"
                "-two\n"
                "+TWO\n"
                "*** End Patch\n"
            )},
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
