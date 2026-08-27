"""Parser and matcher for the Codex apply_patch text format.

This module only understands the patch language and applies an update to text
already supplied by its caller. Workspace path checks, CAS checks, sandbox
permissions, and durable file commits remain the responsibility of the
workspace mutation layer.
"""

from __future__ import annotations

from dataclasses import dataclass

BEGIN_PATCH = "*** Begin Patch"
END_PATCH = "*** End Patch"
ADD_FILE = "*** Add File:"
UPDATE_FILE = "*** Update File:"
DELETE_FILE = "*** Delete File:"
MOVE_TO = "*** Move to:"
END_OF_FILE = "*** End of File"


@dataclass
class PatchError(ValueError):
    """An actionable parser or update-matching error."""

    code: str
    message: str
    line_number: int | None = None
    target_path: str | None = None

    def __post_init__(self) -> None:
        ValueError.__init__(self, self.message)

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class AddFile:
    path: str
    content: str

    @property
    def contents(self) -> str:
        return self.content

    @property
    def kind(self) -> str:
        return "add"


@dataclass(frozen=True, slots=True)
class DeleteFile:
    path: str

    @property
    def kind(self) -> str:
        return "delete"


@dataclass(frozen=True, slots=True)
class UpdateFileChunk:
    """One context-matched replacement within an Update File action."""

    context: str | None
    old_lines: tuple[str, ...]
    new_lines: tuple[str, ...]
    is_end_of_file: bool = False
    line_number: int | None = None

    @property
    def change_context(self) -> str | None:
        return self.context

    @property
    def end_of_file(self) -> bool:
        return self.is_end_of_file


@dataclass(frozen=True, slots=True)
class UpdateFile:
    path: str
    chunks: tuple[UpdateFileChunk, ...]
    move_to: str | None = None
    line_number: int | None = None

    @property
    def move_path(self) -> str | None:
        return self.move_to

    @property
    def kind(self) -> str:
        return "update"


def parse_patch(text: str) -> list[AddFile | UpdateFile | DeleteFile]:
    """Parse a complete Codex patch into ordered file actions.

    The parser accepts the structured Begin/End Patch format and keeps each
    update chunk in source order. An update must contain explicit @@ or
    @@ context headers, so malformed model output remains actionable.
    """

    lines = text.splitlines()
    if not lines:
        _format_error(
            "Patch is empty; the first line must be '*** Begin Patch'",
            1,
        )

    for number, line in enumerate(lines, start=1):
        if any(ord(character) < 32 and character not in {"\t"} for character in line):
            _format_error(
                "Patch contains an unsupported control character",
                number,
            )

    if lines[0].strip() != BEGIN_PATCH:
        _format_error(
            "The first line of the patch must be '*** Begin Patch'",
            1,
        )

    actions: list[AddFile | UpdateFile | DeleteFile] = []
    index = 1
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped == END_PATCH:
            for trailing_index in range(index + 1, len(lines)):
                if lines[trailing_index].strip():
                    _format_error(
                        "Unexpected content after '*** End Patch'",
                        trailing_index + 1,
                    )
            return actions

        if _has_marker(stripped, ADD_FILE):
            action, index = _parse_add_file(lines, index)
            actions.append(action)
            continue
        if _has_marker(stripped, UPDATE_FILE):
            action, index = _parse_update_file(lines, index)
            actions.append(action)
            continue
        if _has_marker(stripped, DELETE_FILE):
            action, index = _parse_delete_file(lines, index)
            actions.append(action)
            continue

        _format_error(
            "Expected a file hunk header ('*** Add File: path', "
            "'*** Update File: path', or '*** Delete File: path') "
            "or '*** End Patch'",
            index + 1,
        )

    _format_error(
        "The last line of the patch must be '*** End Patch'",
        len(lines) + 1,
    )


def apply_update(original: str, action: UpdateFile) -> str:
    """Apply one parsed update action using exact, forward-only matching.

    Matching occurs against the original file lines. Replacements are then
    applied from the end toward the beginning so earlier edits do not shift
    later offsets. Files produced by this function end with the source's
    first observed newline style.
    """

    if not isinstance(action, UpdateFile):
        raise PatchError(
            "patch_format_error",
            "apply_update requires an UpdateFile action",
            target_path=getattr(action, "path", None),
        )

    original_lines, newline, had_final_newline = _split_content_lines(original)
    if not action.chunks:
        return original
    replacements: list[tuple[int, int, tuple[str, ...]]] = []
    search_start = 0

    for chunk in action.chunks:
        if chunk.context is not None:
            context_match = _find_line_sequence(
                original_lines,
                (chunk.context,),
                search_start,
            )
            if context_match is None:
                raise PatchError(
                    "patch_context_mismatch",
                    f"Failed to find context '{chunk.context}' in {action.path}",
                    line_number=chunk.line_number or action.line_number,
                    target_path=action.path,
                )
            search_start = context_match + 1

        if chunk.old_lines:
            match = _find_line_sequence(
                original_lines,
                chunk.old_lines,
                search_start,
                require_end=chunk.is_end_of_file,
            )
            if match is None:
                expected = "\n".join(chunk.old_lines)
                raise PatchError(
                    "patch_context_mismatch",
                    f"Failed to find expected lines in {action.path}:\n{expected}",
                    line_number=chunk.line_number or action.line_number,
                    target_path=action.path,
                )
            replacements.append((match, len(chunk.old_lines), chunk.new_lines))
            search_start = match + len(chunk.old_lines)
            continue

        # Codex treats an addition-only chunk as an append operation. A context
        # header still validates the named context but does not make this an
        # in-place replacement.
        replacements.append((len(original_lines), 0, chunk.new_lines))

    updated_lines = list(original_lines)
    for start, old_length, new_lines in reversed(replacements):
        updated_lines[start : start + old_length] = new_lines
    if not updated_lines:
        return ""
    return newline.join(updated_lines) + (newline if had_final_newline else "")


def _parse_add_file(lines: list[str], header_index: int) -> tuple[AddFile, int]:
    header = lines[header_index].strip()
    path = _path_from_marker(header, ADD_FILE, header_index + 1)
    contents: list[str] = []
    index = header_index + 1

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if _is_hunk_boundary(stripped):
            break
        if not line.startswith("+"):
            _format_error(
                "Add File content must start with '+'; "
                "the next file hunk or End Patch may follow after the content",
                index + 1,
                path,
            )
        contents.append(line[1:])
        index += 1

    if not contents:
        _format_error(
            f"Add File hunk for path '{path}' is empty; provide at least one '+...' line",
            header_index + 1,
            path,
        )
    return AddFile(path=path, content="\n".join(contents) + "\n"), index


def _parse_delete_file(lines: list[str], header_index: int) -> tuple[DeleteFile, int]:
    header = lines[header_index].strip()
    path = _path_from_marker(header, DELETE_FILE, header_index + 1)
    index = header_index + 1
    if index < len(lines) and not _is_hunk_boundary(lines[index].strip()):
        _format_error(
            "Delete File must not contain content; the next file hunk or End Patch "
            "must follow",
            index + 1,
            path,
        )
    return DeleteFile(path=path), index


def _parse_update_file(lines: list[str], header_index: int) -> tuple[UpdateFile, int]:
    header = lines[header_index].strip()
    path = _path_from_marker(header, UPDATE_FILE, header_index + 1)
    action_line = header_index + 1
    index = action_line
    move_to: str | None = None
    if index < len(lines) and _has_marker(lines[index].strip(), MOVE_TO):
        move_to = _path_from_marker(lines[index].strip(), MOVE_TO, index + 1)
        index += 1

    chunks: list[UpdateFileChunk] = []
    current_context: str | None = None
    current_old: list[str] = []
    current_new: list[str] = []
    current_line: int | None = None
    current_eof = False

    def finish_chunk() -> None:
        nonlocal current_context, current_old, current_new, current_line, current_eof
        if current_line is None:
            return
        if not current_old and not current_new:
            _format_error(
                "Update hunk does not contain any change lines",
                current_line,
                path,
            )
        chunks.append(
            UpdateFileChunk(
                context=current_context,
                old_lines=tuple(current_old),
                new_lines=tuple(current_new),
                is_end_of_file=current_eof,
                line_number=current_line,
            )
        )
        current_context = None
        current_old = []
        current_new = []
        current_line = None
        current_eof = False

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if stripped == END_PATCH or _is_file_hunk_header(stripped):
            finish_chunk()
            if not chunks and move_to is None:
                _format_error(
                    f"Update File hunk for path '{path}' is empty; "
                    "provide an @@ change hunk",
                    action_line,
                    path,
                )
            return (
                UpdateFile(
                    path=path,
                    chunks=tuple(chunks),
                    move_to=move_to,
                    line_number=action_line,
                ),
                index,
            )

        if _has_marker(stripped, MOVE_TO):
            _format_error(
                "Move to must appear immediately after Update File and only once",
                index + 1,
                path,
            )

        if stripped == END_OF_FILE:
            if current_line is None:
                _format_error(
                    "'*** End of File' must follow an @@ hunk with change lines",
                    index + 1,
                    path,
                )
            if current_eof:
                _format_error(
                    "An update hunk cannot contain more than one '*** End of File' marker",
                    index + 1,
                    path,
                )
            current_eof = True
            index += 1
            continue

        context = _context_from_header(stripped)
        if context is not None or stripped == "@@":
            finish_chunk()
            current_context = context
            current_line = index + 1
            index += 1
            continue

        if current_eof and not line:
            # Codex tolerates an empty separator after End of File.
            index += 1
            continue

        if current_line is None:
            _format_error(
                "Update hunk lines must start with an '@@' or '@@ context' header",
                index + 1,
                path,
            )
        if line == "":
            current_old.append("")
            current_new.append("")
        elif line.startswith(" "):
            value = line[1:]
            current_old.append(value)
            current_new.append(value)
        elif line.startswith("+"):
            current_new.append(line[1:])
        elif line.startswith("-"):
            current_old.append(line[1:])
        else:
            _format_error(
                "Invalid update hunk line; every change line must start with ' ', '+' or '-'",
                index + 1,
                path,
            )
        index += 1

    finish_chunk()
    if not chunks and move_to is None:
        _format_error(
            f"Update File hunk for path '{path}' is empty; provide an @@ change hunk",
            action_line,
            path,
        )
    _format_error(
        "The last line of the patch must be '*** End Patch'",
        len(lines) + 1,
        path,
    )


def _split_content_lines(content: str) -> tuple[list[str], str, bool]:
    pieces = content.splitlines(keepends=True)
    if not pieces:
        return [], "\n", False

    values: list[str] = []
    newline = "\n"
    found_newline = False
    for piece in pieces:
        if piece.endswith("\r\n"):
            values.append(piece[:-2])
            if not found_newline:
                newline = "\r\n"
                found_newline = True
        elif piece.endswith("\n") or piece.endswith("\r"):
            values.append(piece[:-1])
            if not found_newline:
                newline = piece[-1]
                found_newline = True
        else:
            values.append(piece)
    had_final_newline = pieces[-1].endswith(("\r\n", "\n", "\r"))
    return values, newline, had_final_newline


def _find_line_sequence(
    source: list[str],
    pattern: tuple[str, ...],
    start: int,
    *,
    require_end: bool = False,
) -> int | None:
    if not pattern:
        return start
    if start < 0:
        start = 0
    last_start = len(source) - len(pattern)
    if last_start < start:
        return None
    if require_end:
        candidate = last_start
        if candidate < start:
            return None
        return (
            candidate
            if tuple(source[candidate : candidate + len(pattern)]) == pattern
            else None
        )
    for candidate in range(start, last_start + 1):
        if tuple(source[candidate : candidate + len(pattern)]) == pattern:
            return candidate
    return None


def _has_marker(line: str, marker: str) -> bool:
    return (
        line == marker
        or line.startswith(marker + " ")
        or line.startswith(marker + "\t")
    )


def _path_from_marker(line: str, marker: str, line_number: int) -> str:
    if not _has_marker(line, marker):
        _format_error(f"Expected '{marker} path'", line_number)
    path = line[len(marker) :].strip()
    if not path:
        _format_error(f"'{marker} path' must include a non-empty path", line_number)
    if any(character in path for character in {"\x00", "\n", "\r"}):
        _format_error(
            "Patch paths cannot contain control characters", line_number, path
        )
    return path


def _context_from_header(line: str) -> str | None:
    if line.startswith("@@ "):
        context = line[3:]
        if not context:
            return None
        return context
    return None


def _is_file_hunk_header(line: str) -> bool:
    return any(
        _has_marker(line, marker) for marker in (ADD_FILE, UPDATE_FILE, DELETE_FILE)
    )


def _is_hunk_boundary(line: str) -> bool:
    return line == END_PATCH or _is_file_hunk_header(line)


def _format_error(
    message: str,
    line_number: int,
    target_path: str | None = None,
) -> None:
    raise PatchError(
        "patch_format_error",
        message,
        line_number=line_number,
        target_path=target_path,
    )


__all__ = [
    "AddFile",
    "DeleteFile",
    "PatchError",
    "UpdateFile",
    "UpdateFileChunk",
    "apply_update",
    "parse_patch",
]
