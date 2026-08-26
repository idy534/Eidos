from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
from typing import Literal

from unidiff import PatchSet


class PatchApplyError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class _PositionIndependentHunk:
    context: str | None
    old_lines: tuple[str, ...]
    new_lines: tuple[str, ...]


_UNSUPPORTED_PREFIXES = (
    "diff --git ",
    "diff --cc ",
    "diff --combined ",
    "index ",
    "new file mode ",
    "deleted file mode ",
    "old mode ",
    "new mode ",
    "similarity index ",
    "rename from ",
    "rename to ",
    "copy from ",
    "copy to ",
    "Binary files ",
    "GIT binary patch",
)


def apply_strict_single_file_patch(
    *,
    path: str,
    original: str,
    patch_text: str,
) -> str:
    """Apply one exact single-file standard or position-independent patch."""
    _reject_unsupported_patch_structure(patch_text)
    position_independent_hunks = _parse_position_independent_patch(path, patch_text)
    if position_independent_hunks is not None:
        return _apply_position_independent_hunks(original, position_independent_hunks)
    try:
        patch_set = PatchSet(StringIO(patch_text), metadata_only=False)
    except Exception:
        raise PatchApplyError("invalid_patch") from None

    if len(patch_set) != 1:
        raise PatchApplyError("invalid_patch")
    patched_file = patch_set[0]
    _validate_patched_file(path, patched_file)
    return _apply_hunks(original, patched_file)


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


def _reject_unsupported_patch_structure(patch_text: str) -> None:
    if any(
        ord(character) < 32 and character not in {"\n", "\r", "\t"}
        for character in patch_text
    ):
        raise PatchApplyError("invalid_patch")
    for line in patch_text.splitlines(keepends=True):
        bare = line.rstrip("\n")
        if bare.startswith("\\ No newline at end of file"):
            raise PatchApplyError("invalid_patch")
        if bare.startswith(_UNSUPPORTED_PREFIXES):
            raise PatchApplyError("invalid_patch")
        if bare.startswith("@@ ") and bare.endswith("\r"):
            raise PatchApplyError("invalid_patch")
        if bare != "@@" and not bare.startswith(
            ("--- ", "+++ ", "@@ ", " ", "+", "-")
        ):
            raise PatchApplyError("invalid_patch")


def _parse_position_independent_patch(
    path: str, patch_text: str
) -> list[_PositionIndependentHunk] | None:
    lines = patch_text.splitlines(keepends=True)
    if not any(_is_position_independent_hunk_header(line) for line in lines):
        return None
    if len(lines) < 3:
        raise PatchApplyError("invalid_patch")

    source_path = _parse_position_independent_file_header(lines[0], "--- ")
    target_path = _parse_position_independent_file_header(lines[1], "+++ ")
    if source_path not in {path, f"a/{path}"} or target_path not in {
        path,
        f"b/{path}",
    }:
        raise PatchApplyError("invalid_patch")

    hunks: list[_PositionIndependentHunk] = []
    index = 2
    while index < len(lines):
        is_header, context = _position_independent_hunk_header(lines[index])
        if not is_header:
            raise PatchApplyError("invalid_patch")
        index += 1
        old_lines: list[str] = []
        new_lines: list[str] = []
        while index < len(lines):
            next_is_header, _ = _position_independent_hunk_header(lines[index])
            if next_is_header:
                break
            if (
                lines[index].startswith("--- ")
                and index + 1 < len(lines)
                and lines[index + 1].startswith("+++ ")
            ):
                raise PatchApplyError("invalid_patch")
            line = lines[index]
            if not line or line[0] not in {" ", "+", "-"}:
                raise PatchApplyError("invalid_patch")
            value = line[1:]
            if line[0] == "+":
                new_lines.append(value)
            elif line[0] == "-":
                old_lines.append(value)
            else:
                old_lines.append(value)
                new_lines.append(value)
            index += 1
        if not old_lines and not new_lines:
            raise PatchApplyError("invalid_patch")
        hunks.append(
            _PositionIndependentHunk(
                context=context,
                old_lines=tuple(old_lines),
                new_lines=tuple(new_lines),
            )
        )
    return hunks


def _parse_position_independent_file_header(line: str, prefix: str) -> str:
    value = _strict_structure_line(line)
    if value is None or not value.startswith(prefix) or not value[len(prefix) :]:
        raise PatchApplyError("invalid_patch")
    return value[len(prefix) :]


def _strict_structure_line(line: str) -> str | None:
    if line.endswith("\n"):
        line = line[:-1]
        if line.endswith("\r"):
            return None
    return line


def _is_position_independent_hunk_header(line: str) -> bool:
    is_header, _ = _position_independent_hunk_header(line)
    return is_header


def _position_independent_hunk_header(line: str) -> tuple[bool, str | None]:
    value = _strict_structure_line(line)
    if value == "@@":
        return True, None
    if value is None or not value.startswith("@@ ") or value.startswith("@@ -"):
        return False, None
    context = value[3:]
    if not context:
        raise PatchApplyError("invalid_patch")
    return True, context


def _apply_position_independent_hunks(
    original: str, hunks: list[_PositionIndependentHunk]
) -> str:
    original_lines = original.splitlines(keepends=True)
    replacements: list[tuple[int, int, tuple[str, ...]]] = []
    source_cursor = 0
    for hunk in hunks:
        search_start = source_cursor
        context_index: int | None = None
        if hunk.context is not None:
            context_matches = [
                line_number
                for line_number in range(source_cursor, len(original_lines))
                if _line_without_ending(original_lines[line_number]) == hunk.context
            ]
            if len(context_matches) != 1:
                raise PatchApplyError("patch_context_mismatch")
            context_index = context_matches[0]
            search_start = context_index + 1

        if hunk.old_lines:
            matches = _find_exact_line_sequence(
                original_lines, hunk.old_lines, search_start
            )
            if len(matches) != 1:
                raise PatchApplyError("patch_context_mismatch")
            start = matches[0]
            replacements.append((start, len(hunk.old_lines), hunk.new_lines))
            source_cursor = start + len(hunk.old_lines)
        else:
            insertion_index = (
                len(original_lines)
                if context_index is None
                else context_index + 1
            )
            replacements.append((insertion_index, 0, hunk.new_lines))
            source_cursor = insertion_index

    updated_lines = list(original_lines)
    for start in sorted({replacement[0] for replacement in replacements}, reverse=True):
        at_start = [replacement for replacement in replacements if replacement[0] == start]
        if len(at_start) == 1:
            _, old_length, new_lines = at_start[0]
            updated_lines[start : start + old_length] = new_lines
            continue
        if any(replacement[1] for replacement in at_start):
            raise PatchApplyError("patch_context_mismatch")
        inserted_lines = tuple(
            line for _, _, new_lines in at_start for line in new_lines
        )
        updated_lines[start:start] = inserted_lines
    return "".join(updated_lines)


def _line_without_ending(line: str) -> str:
    if line.endswith("\n"):
        line = line[:-1]
        if line.endswith("\r"):
            line = line[:-1]
    return line


def _find_exact_line_sequence(
    source: list[str], pattern: tuple[str, ...], start: int
) -> list[int]:
    if not pattern or start > len(source) - len(pattern):
        return []
    source_offsets = [0]
    for line in source:
        source_offsets.append(source_offsets[-1] + len(line))
    pattern_text = "".join(pattern)
    if not pattern_text:
        return []
    source_text = "".join(source)
    line_starts = {
        source_offsets[index]: index
        for index in range(start, len(source) - len(pattern) + 1)
    }
    line_boundaries = frozenset(source_offsets)
    matches: list[int] = []
    offset = source_text.find(pattern_text, source_offsets[start])
    while offset >= 0:
        line_index = line_starts.get(offset)
        if (
            line_index is not None
            and offset + len(pattern_text) in line_boundaries
        ):
            matches.append(line_index)
            if len(matches) > 1:
                break
        offset = source_text.find(pattern_text, offset + 1)
    return matches


def _validate_patched_file(path: str, patched_file: object) -> None:
    source_paths = {path, f"a/{path}"}
    target_paths = {path, f"b/{path}"}
    source_file = getattr(patched_file, "source_file", None)
    target_file = getattr(patched_file, "target_file", None)
    if (
        not patched_file
        or source_file not in source_paths
        or target_file not in target_paths
        or getattr(patched_file, "source_timestamp", None) is not None
        or getattr(patched_file, "target_timestamp", None) is not None
        or getattr(patched_file, "patch_info", None) is not None
        or getattr(patched_file, "source_mode", None) is not None
        or getattr(patched_file, "target_mode", None) is not None
        or getattr(patched_file, "is_binary_file", False)
        or getattr(patched_file, "is_rename", False)
        or getattr(patched_file, "is_symlink", False)
    ):
        raise PatchApplyError("invalid_patch")


def _apply_hunks(original: str, patched_file: object) -> str:
    original_lines = original.splitlines(keepends=True)
    candidate: list[str] = []
    source_cursor = 0
    for hunk in patched_file:
        source_start = hunk.source_start
        target_cursor = 0 if source_start == 0 else source_start - 1
        if source_start < 0 or target_cursor < source_cursor or target_cursor > len(original_lines):
            raise PatchApplyError("patch_context_mismatch")
        candidate.extend(original_lines[source_cursor:target_cursor])
        source_cursor = target_cursor
        consumed = 0
        produced = 0
        for line in hunk:
            if line.is_context:
                _require_source_line(original_lines, source_cursor, line.value)
                candidate.append(line.value)
                source_cursor += 1
                consumed += 1
                produced += 1
            elif line.is_removed:
                _require_source_line(original_lines, source_cursor, line.value)
                source_cursor += 1
                consumed += 1
            elif line.is_added:
                candidate.append(line.value)
                produced += 1
            else:
                raise PatchApplyError("invalid_patch")
        if (
            consumed != hunk.source_length
            or produced != hunk.target_length
            or not hunk.is_valid()
        ):
            raise PatchApplyError("invalid_patch")
    candidate.extend(original_lines[source_cursor:])
    return "".join(candidate)


def _require_source_line(source: list[str], cursor: int, value: str) -> None:
    if cursor >= len(source) or source[cursor] != value:
        raise PatchApplyError("patch_context_mismatch")
