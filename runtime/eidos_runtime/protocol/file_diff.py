from __future__ import annotations

from io import StringIO
import json

from unidiff import PatchSet, UnidiffParseError


def display_file_diff(diff: str) -> str:
    """Add Git file headers for Desktop without changing stored patch evidence.

    react-diff-view assumes timestamps on plain Unified Diff headers. unidiff
    supplies file boundaries, including legacy multi-file difflib output; the
    original hunks and Eidos newline metadata remain byte-for-byte unchanged.
    """
    if not diff or len(diff) > 2 * 1024 * 1024:
        return diff
    try:
        files = PatchSet(StringIO(diff))
    except (UnidiffParseError, ValueError, IndexError):
        return diff
    lines = diff.splitlines(keepends=True)
    for file in reversed(files):
        target_line = file.diff_line_no
        if target_line is None or target_line < 2 or target_line > len(lines):
            continue
        index = target_line - 2
        # A Git file's diff_line_no points at its existing Git header instead.
        if lines[index] != f"--- {file.source_file}\n" or lines[index + 1] != f"+++ {file.target_file}\n":
            continue
        source = file.source_file if file.source_file != "/dev/null" else f"a/{file.path}"
        target = file.target_file if file.target_file != "/dev/null" else f"b/{file.path}"
        lines.insert(index, f"diff --git {json.dumps(source, ensure_ascii=False)} {json.dumps(target, ensure_ascii=False)}\n")
    return "".join(lines)
