from __future__ import annotations

import pytest

from eidos_runtime.workspace.codex_patch import (
    AddFile,
    DeleteFile,
    PatchError,
    UpdateFile,
    apply_update,
    encode_patch,
    parse_patch,
)
from eidos_runtime.tools.contracts import ApplyPatchInput


def test_parse_add_update_delete_and_move_actions_in_source_order() -> None:
    actions = parse_patch(
        "*** Begin Patch\n"
        "*** Add File: src/new.py\n"
        "+first\n"
        "+second\n"
        "*** Update File: src/old.py\n"
        "*** Move to: generated/old.py\n"
        "@@\n"
        "-old\n"
        "+new\n"
        "*** Delete File: obsolete.py\n"
        "*** End Patch"
    )

    assert [type(action) for action in actions] == [AddFile, UpdateFile, DeleteFile]
    assert actions[0].path == "src/new.py"
    assert actions[0].content == "first\nsecond\n"
    assert actions[1].path == "src/old.py"
    assert actions[1].move_to == "generated/old.py"
    assert actions[1].chunks[0].old_lines == ("old",)
    assert actions[1].chunks[0].new_lines == ("new",)
    assert actions[2].path == "obsolete.py"


def test_parse_move_only_update_without_change_chunks() -> None:
    action = parse_patch(
        "*** Begin Patch\n"
        "*** Update File: old.txt\n"
        "*** Move to: nested/new.txt\n"
        "*** End Patch"
    )[0]

    assert isinstance(action, UpdateFile)
    assert action.move_to == "nested/new.txt"
    assert action.chunks == ()
    assert apply_update("unchanged", action) == "unchanged"


def test_update_supports_bare_and_context_headers_with_multiple_chunks() -> None:
    actions = parse_patch(
        "*** Begin Patch\n"
        "*** Update File: events.ts\n"
        "@@\n"
        "-first\n"
        "+start\n"
        "@@ function handle()\n"
        "-old\n"
        "+new\n"
        "*** End Patch"
    )

    updated = apply_update("first\nfunction handle()\nold\nlast\n", actions[0])
    assert updated == "start\nfunction handle()\nnew\nlast\n"


def test_update_supports_bare_first_change_without_context_header() -> None:
    action = parse_patch(
        "*** Begin Patch\n"
        "*** Update File: notes.txt\n"
        "-old\n"
        "+new\n"
        "*** End Patch"
    )[0]

    assert isinstance(action, UpdateFile)
    assert action.chunks[0].context is None
    assert apply_update("before\nold\nafter\n", action) == "before\nnew\nafter\n"


def test_update_preserves_marker_text_in_space_prefixed_context_lines() -> None:
    action = parse_patch(
        "*** Begin Patch\n"
        "*** Update File: notes.txt\n"
        " *** End Patch\n"
        "+new\n"
        "*** End Patch"
    )[0]

    assert isinstance(action, UpdateFile)
    assert action.chunks[0].old_lines == ("*** End Patch",)
    assert action.chunks[0].new_lines == ("*** End Patch", "new")


def test_parse_normalizes_crlf_patch_input_and_preserves_unicode_lines() -> None:
    patch = "\r\n".join(
        [
            "*** Begin Patch",
            "*** Add File: 文档/新文件.txt",
            "+你好，世界 🌍",
            "*** Update File: src/文件.py",
            "*** Move to: src/已移动.py",
            "@@ 函数 😀",
            "-旧值",
            "+新值",
            "*** End of File",
            "*** Delete File: 删除.txt",
            "*** End Patch",
        ]
    ) + "\r\n"

    actions = parse_patch(patch)

    assert [type(action) for action in actions] == [AddFile, UpdateFile, DeleteFile]
    assert actions[0].path == "文档/新文件.txt"
    assert actions[0].content == "你好，世界 🌍\n"
    assert actions[1].path == "src/文件.py"
    assert actions[1].move_to == "src/已移动.py"
    assert actions[1].chunks[0].context == "函数 😀"
    assert actions[1].chunks[0].old_lines == ("旧值",)
    assert actions[1].chunks[0].new_lines == ("新值",)
    assert actions[1].chunks[0].is_end_of_file is True
    assert actions[2].path == "删除.txt"


def test_parse_trims_outer_patch_whitespace() -> None:
    actions = parse_patch(
        "\n  *** Begin Patch  \n"
        "*** Delete File: obsolete.txt\n"
        "  *** End Patch  \n"
    )

    assert actions == [DeleteFile(path="obsolete.txt")]


def test_parse_rejects_patch_without_file_hunks() -> None:
    with pytest.raises(PatchError) as raised:
        parse_patch("*** Begin Patch\n*** End Patch")

    assert raised.value.code == "patch_format_error"
    assert raised.value.line_number is not None


def test_encode_patch_builds_canonical_multifile_unicode_patch() -> None:
    request = ApplyPatchInput.model_validate(
        {
            "changes": [
                {
                    "type": "add",
                    "path": "文档/新文件.txt",
                    "content": "你好\n\n世界\n",
                },
                {
                    "type": "update",
                    "path": "src/旧.py",
                    "moveTo": "src/新.py",
                    "chunks": [
                        {
                            "context": "函数 😀",
                            "oldLines": ["旧值"],
                            "newLines": ["新值"],
                            "endOfFile": True,
                        },
                    ],
                },
                {"type": "delete", "path": "删除.txt"},
            ]
        },
        strict=False,
    )

    encoded = encode_patch(request)

    assert encoded == (
        "*** Begin Patch\n"
        "*** Add File: 文档/新文件.txt\n"
        "+你好\n"
        "+\n"
        "+世界\n"
        "*** Update File: src/旧.py\n"
        "*** Move to: src/新.py\n"
        "@@ 函数 😀\n"
        "-旧值\n"
        "+新值\n"
        "*** End of File\n"
        "*** Delete File: 删除.txt\n"
        "*** End Patch"
    )
    assert "\r" not in encoded
    assert [type(action) for action in parse_patch(encoded)] == [
        AddFile,
        UpdateFile,
        DeleteFile,
    ]


def test_encode_patch_preserves_empty_add_file_without_a_plus_line() -> None:
    request = ApplyPatchInput.model_validate(
        {"changes": [{"type": "add", "path": "empty.txt", "content": ""}]},
        strict=False,
    )

    encoded = encode_patch(request)

    assert encoded == (
        "*** Begin Patch\n"
        "*** Add File: empty.txt\n"
        "*** End Patch"
    )
    assert parse_patch(encoded)[0].content == ""


def test_parse_preserves_trailing_space_in_file_path() -> None:
    request = ApplyPatchInput.model_validate(
        {"changes": [{"type": "add", "path": "a ", "content": "x"}]},
        strict=False,
    )

    encoded = encode_patch(request)

    assert encoded == (
        "*** Begin Patch\n"
        "*** Add File: a \n"
        "+x\n"
        "*** End Patch"
    )
    assert parse_patch(encoded)[0].path == "a "


def test_apply_update_supports_pure_insertion() -> None:
    action = parse_patch(
        "*** Begin Patch\n"
        "*** Update File: notes.txt\n"
        "@@\n"
        "+tail\n"
        "*** End Patch"
    )[0]

    assert isinstance(action, UpdateFile)
    assert action.chunks[0].old_lines == ()
    assert action.chunks[0].new_lines == ("tail",)
    assert apply_update("head\n", action) == "head\ntail\n"


def test_apply_update_supports_pure_deletion() -> None:
    action = parse_patch(
        "*** Begin Patch\n"
        "*** Update File: notes.txt\n"
        "@@\n"
        "-remove\n"
        "*** End Patch"
    )[0]

    assert isinstance(action, UpdateFile)
    assert action.chunks[0].old_lines == ("remove",)
    assert action.chunks[0].new_lines == ()
    assert apply_update("keep\nremove\n", action) == "keep\n"


def test_encode_patch_rejects_end_of_file_before_last_chunk() -> None:
    request = ApplyPatchInput.model_validate(
        {
            "changes": [
                {
                    "type": "update",
                    "path": "notes.txt",
                    "chunks": [
                        {
                            "oldLines": ["first"],
                            "newLines": ["one"],
                            "endOfFile": True,
                        },
                        {"oldLines": ["second"], "newLines": ["two"]},
                    ],
                }
            ]
        },
        strict=False,
    )

    with pytest.raises(PatchError, match="final update chunk"):
        encode_patch(request)


def test_end_of_file_requires_the_last_chunk_lines_at_file_end() -> None:
    action = parse_patch(
        "*** Begin Patch\n"
        "*** Update File: app.py\n"
        "@@\n"
        "-three\n"
        "+THREE\n"
        "*** End of File\n"
        "*** End Patch"
    )[0]

    assert action.chunks[0].is_end_of_file is True
    assert apply_update("one\ntwo\nthree\n", action) == "one\ntwo\nTHREE\n"

    with pytest.raises(PatchError) as raised:
        apply_update("three\none\n", action)
    assert raised.value.code == "patch_context_mismatch"
    assert raised.value.target_path == "app.py"
    assert "Failed to find expected lines in app.py" in raised.value.message


def test_context_and_expected_line_errors_are_actionable() -> None:
    context_action = parse_patch(
        "*** Begin Patch\n"
        "*** Update File: events.ts\n"
        "@@ missing context\n"
        "-old\n"
        "+new\n"
        "*** End Patch"
    )[0]
    with pytest.raises(PatchError) as context_error:
        apply_update("actual\n", context_action)
    assert context_error.value.code == "patch_context_mismatch"
    assert context_error.value.target_path == "events.ts"
    assert context_error.value.message == (
        "Failed to find context 'missing context' in events.ts"
    )

    expected_action = parse_patch(
        "*** Begin Patch\n*** Update File: events.ts\n@@\n-old\n+new\n*** End Patch"
    )[0]
    with pytest.raises(PatchError) as expected_error:
        apply_update("actual\n", expected_action)
    assert (
        expected_error.value.message
        == "Failed to find expected lines in events.ts:\nold"
    )


def test_matching_is_exact_and_forward_only() -> None:
    action = parse_patch(
        "*** Begin Patch\n"
        "*** Update File: notes.txt\n"
        "@@\n"
        "-first\n"
        "+FIRST\n"
        "@@\n"
        "-second\n"
        "+SECOND\n"
        "*** End Patch"
    )[0]
    assert apply_update("first\nsecond\n", action) == "FIRST\nSECOND\n"

    out_of_order = parse_patch(
        "*** Begin Patch\n"
        "*** Update File: notes.txt\n"
        "@@\n"
        "-second\n"
        "+SECOND\n"
        "@@\n"
        "-first\n"
        "+FIRST\n"
        "*** End Patch"
    )[0]
    with pytest.raises(PatchError, match="Failed to find expected lines"):
        apply_update("first\nsecond\n", out_of_order)


def test_parse_reports_format_error_location_and_target() -> None:
    with pytest.raises(PatchError) as raised:
        parse_patch(
            "*** Begin Patch\n*** Update File: src/events.ts\n!old\n+new\n*** End Patch"
        )

    error = raised.value
    assert error.code == "patch_format_error"
    assert error.line_number == 3
    assert error.target_path == "src/events.ts"
    assert "hunk" in error.message.lower()
    assert error.__suppress_context__ is True
    assert "Traceback" not in error.message


def test_parse_rejects_malformed_lines_and_missing_boundaries() -> None:
    malformed = "*** Begin Patch\n*** Add File: new.txt\nnot an add line\n*** End Patch"
    with pytest.raises(PatchError) as malformed_error:
        parse_patch(malformed)
    assert malformed_error.value.code == "patch_format_error"
    assert malformed_error.value.line_number == 3
    assert "add" in malformed_error.value.message.lower()

    with pytest.raises(PatchError) as boundary_error:
        parse_patch("*** Add File: new.txt\n+content\n*** End Patch")
    assert boundary_error.value.code == "patch_format_error"
    assert boundary_error.value.line_number == 1
    assert "begin patch" in boundary_error.value.message.lower()


def test_update_preserves_original_line_ending_style() -> None:
    action = parse_patch(
        "*** Begin Patch\n*** Update File: notes.txt\n@@\n-old\n+new\n*** End Patch"
    )[0]
    assert apply_update("first\r\nold\r\nlast\r\n", action) == (
        "first\r\nnew\r\nlast\r\n"
    )


def test_update_preserves_missing_final_newline() -> None:
    action = parse_patch(
        "*** Begin Patch\n*** Update File: notes.txt\n@@\n-old\n+new\n*** End Patch"
    )[0]
    assert apply_update("old", action) == "new"
