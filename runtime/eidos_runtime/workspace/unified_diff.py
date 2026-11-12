from __future__ import annotations

from io import StringIO
from typing import Literal

from unidiff import PatchSet


def validate_diff_anchor(
    patch_text: str,
    *,
    path: str,
    side: Literal["old", "new"],
    line: int,
) -> bool:
    """Return whether a line anchor exists in the current parsed patch."""
    if line < 1:
        return False
    try:
        patch_set = PatchSet(StringIO(patch_text), metadata_only=False)
    except Exception:
        return False
    for patched_file in patch_set:
        if not _patch_file_matches(path, patched_file):
            continue
        for hunk in patched_file:
            for patch_line in hunk:
                line_number = (
                    patch_line.source_line_no
                    if side == "old"
                    else patch_line.target_line_no
                )
                if line_number != line:
                    continue
                return patch_line.is_context or (
                    side == "old" and patch_line.is_removed
                ) or (side == "new" and patch_line.is_added)
    return False


def _patch_file_matches(path: str, patched_file: object) -> bool:
    source_file = getattr(patched_file, "source_file", None)
    target_file = getattr(patched_file, "target_file", None)
    return source_file in {path, f"a/{path}"} or target_file in {
        path,
        f"b/{path}",
    }
