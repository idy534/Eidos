from __future__ import annotations

from io import StringIO

from unidiff import PatchSet


class PatchApplyError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


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
    """Parse and apply Eidos's strict, exact-context single-file Patch form."""
    _reject_unsupported_patch_structure(patch_text)
    try:
        patch_set = PatchSet(StringIO(patch_text), metadata_only=False)
    except Exception:
        raise PatchApplyError("invalid_patch") from None

    if len(patch_set) != 1:
        raise PatchApplyError("invalid_patch")
    patched_file = patch_set[0]
    _validate_patched_file(path, patched_file)
    return _apply_hunks(original, patched_file)


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
        if not bare.startswith(("--- ", "+++ ", "@@ ", " ", "+", "-")):
            raise PatchApplyError("invalid_patch")


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
