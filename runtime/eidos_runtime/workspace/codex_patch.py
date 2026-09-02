"""Parser and matcher for the Codex apply_patch text format.

This module only understands the patch language and applies an update to text
already supplied by its caller. Workspace path checks, CAS checks, sandbox
permissions, and durable file commits remain the responsibility of the
workspace mutation layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

from lark import Lark, Token, Tree
from lark.exceptions import LarkError, UnexpectedInput

from eidos_runtime.model.client import MAX_CUSTOM_TOOL_INPUT_BYTES

if TYPE_CHECKING:
    from eidos_runtime.tools.contracts import ApplyPatchInput

BEGIN_PATCH = "*** Begin Patch"
END_PATCH = "*** End Patch"
ADD_FILE = "*** Add File:"
UPDATE_FILE = "*** Update File:"
DELETE_FILE = "*** Delete File:"
MOVE_TO = "*** Move to:"
END_OF_FILE = "*** End of File"
MAX_PATCH_BYTES = MAX_CUSTOM_TOOL_INPUT_BYTES

_PATCH_PARSER = Lark.open(
    str(Path(__file__).with_name("apply_patch.lark")),
    parser="lalr",
    maybe_placeholders=False,
    propagate_positions=True,
)


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

    The Lark grammar is the syntax authority. This function only normalizes
    transport line endings, maps syntax failures to the product error type,
    and converts the parse tree into the existing domain actions.
    """

    if not isinstance(text, str):
        raise PatchError("patch_format_error", "Patch must be text")
    try:
        input_bytes = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        raise PatchError("invalid_utf8", "Patch must be valid UTF-8") from None
    if input_bytes > MAX_PATCH_BYTES:
        raise PatchError("patch_too_large", "Patch exceeds the 512 KiB limit")
    normalized = _normalize_patch_text(text)
    _reject_control_characters(normalized)
    try:
        tree = _PATCH_PARSER.parse(normalized)
    except UnexpectedInput as error:
        _raise_lark_format_error(normalized, error)
    except LarkError:
        # Keep parser implementation details, including grammar internals and
        # stack traces, out of the stable ToolResult surface.
        _format_error("Patch format error", _line_count(normalized))
    return _actions_from_tree(tree)


def patch_grammar() -> str:
    """Return the grammar used by both native custom tools and the parser."""

    return Path(__file__).with_name("apply_patch.lark").read_text(encoding="utf-8")


def encode_patch(request: "ApplyPatchInput") -> str:
    """Encode structured ApplyPatch input into canonical Codex patch text.

    This encoder has no filesystem or matching responsibilities. It only
    writes the syntax that :func:`parse_patch` consumes, using LF regardless of
    the input content's line ending style.
    """

    changes = getattr(request, "changes", None)
    if not changes:
        raise PatchError(
            "patch_format_error",
            "Patch must contain at least one file change",
        )

    lines = [BEGIN_PATCH]
    for change in changes:
        kind = getattr(change, "type", None)
        path = _encoder_path(change, "path")
        if kind == "add":
            content = _encoder_text(change, "content")
            lines.append(f"{ADD_FILE} {path}")
            normalized_content = _normalize_line_endings(content)
            content_lines = normalized_content.split("\n")
            if content_lines[-1] == "":
                content_lines.pop()
            lines.extend(f"+{line}" for line in content_lines)
            continue

        if kind == "delete":
            lines.append(f"{DELETE_FILE} {path}")
            continue

        if kind == "update":
            move_to = getattr(change, "moveTo", None)
            if move_to is not None:
                move_to = _validate_encoder_line(move_to, "moveTo")
                if not move_to:
                    raise PatchError(
                        "patch_format_error",
                        "Update moveTo must be a non-empty path",
                        target_path=path,
                    )
            chunks = tuple(getattr(change, "chunks", ()))
            if not chunks and move_to is None:
                raise PatchError(
                    "patch_format_error",
                    "Update change must contain chunks or moveTo",
                    target_path=path,
                )
            lines.append(f"{UPDATE_FILE} {path}")
            if move_to is not None:
                lines.append(f"{MOVE_TO} {move_to}")
            for index, chunk in enumerate(chunks):
                context = getattr(chunk, "context", None)
                if context is None or context == "":
                    lines.append("@@")
                else:
                    lines.append(f"@@ {_validate_encoder_line(context, 'context')}")
                old_lines = tuple(getattr(chunk, "oldLines", ()))
                new_lines = tuple(getattr(chunk, "newLines", ()))
                if not old_lines and not new_lines:
                    raise PatchError(
                        "patch_format_error",
                        "Update chunk must contain oldLines or newLines",
                        target_path=path,
                    )
                lines.extend(
                    f"-{_validate_encoder_line(line, 'oldLines')}"
                    for line in old_lines
                )
                lines.extend(
                    f"+{_validate_encoder_line(line, 'newLines')}"
                    for line in new_lines
                )
                if getattr(chunk, "endOfFile", False):
                    if index != len(chunks) - 1:
                        raise PatchError(
                            "patch_format_error",
                            "'*** End of File' must be on the final update chunk",
                            target_path=path,
                        )
                    lines.append(END_OF_FILE)
            continue

        raise PatchError(
            "patch_format_error",
            "Unsupported ApplyPatch change type",
            target_path=path,
        )

    lines.append(END_PATCH)
    patch = "\n".join(lines)
    if len(patch.encode("utf-8")) > MAX_PATCH_BYTES:
        raise PatchError("patch_too_large", "Patch exceeds the 512 KiB limit")
    return patch


def _normalize_patch_text(text: str) -> str:
    if not isinstance(text, str):
        raise PatchError("patch_format_error", "Patch must be text")
    normalized = _normalize_line_endings(text)
    lines = normalized.split("\n")
    # Codex trims the transport wrapper before parsing. Remove only blank
    # wrapper lines here. A global ``strip`` would remove the leading space
    # from a valid update context line such as `` *** End Patch``.
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    # Only the two boundary markers are wrapper syntax without payload. Keep
    # all internal lines byte-for-byte after newline normalization so a path
    # such as ``a `` is not silently changed to ``a``.
    if lines and lines[0].strip() == BEGIN_PATCH:
        lines[0] = BEGIN_PATCH
    if lines and lines[-1].strip() == END_PATCH:
        lines[-1] = END_PATCH
    return "\n".join(lines)


def _normalize_line_endings(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _reject_control_characters(text: str) -> None:
    for line_number, line in enumerate(text.split("\n"), start=1):
        if any(ord(character) < 32 and character != "\t" for character in line):
            _format_error(
                "Patch contains an unsupported control character",
                line_number,
            )


def _actions_from_tree(
    tree: Tree,
) -> list[AddFile | UpdateFile | DeleteFile]:
    actions: list[AddFile | UpdateFile | DeleteFile] = []
    for hunk in (
        child
        for child in tree.children
        if isinstance(child, Tree) and child.data == "hunk"
    ):
        if not hunk.children or not isinstance(hunk.children[0], Tree):
            _format_error("Patch contains an invalid file hunk", _node_line(hunk))
        action_node = hunk.children[0]
        if action_node.data == "add_hunk":
            actions.append(_add_action_from_tree(action_node))
        elif action_node.data == "delete_hunk":
            actions.append(_delete_action_from_tree(action_node))
        elif action_node.data == "update_hunk":
            actions.append(_update_action_from_tree(action_node))
        else:
            _format_error("Patch contains an invalid file hunk", _node_line(hunk))
    if not actions:
        _format_error(
            "Patch must contain at least one file hunk",
            _line_count_from_tree(tree),
        )
    return actions


def _add_action_from_tree(node: Tree) -> AddFile:
    path = _tree_filename(node)
    content = "".join(
        f"{_tree_token(line, 'ADD_TEXT', default='')}\n"
        for line in node.children
        if isinstance(line, Tree) and line.data == "line"
    )
    return AddFile(path=path, content=content)


def _delete_action_from_tree(node: Tree) -> DeleteFile:
    return DeleteFile(path=_tree_filename(node))


def _update_action_from_tree(node: Tree) -> UpdateFile:
    path = _tree_filename(node)
    move_node = next(
        (
            child
            for child in node.children
            if isinstance(child, Tree) and child.data == "change_move"
        ),
        None,
    )
    move_to = _tree_filename(move_node) if move_node is not None else None
    change_node = next(
        (
            child
            for child in node.children
            if isinstance(child, Tree) and child.data == "change"
        ),
        None,
    )
    chunks = _chunks_from_tree(change_node, path) if change_node is not None else ()
    if not chunks and move_to is None:
        _format_error(
            f"Update File hunk for path '{path}' is empty; provide an @@ change hunk",
            _node_line(node) + 1,
            path,
        )
    return UpdateFile(
        path=path,
        chunks=chunks,
        move_to=move_to,
        line_number=_node_line(node) + 1,
    )


def _chunks_from_tree(node: Tree, path: str) -> tuple[UpdateFileChunk, ...]:
    chunks: list[UpdateFileChunk] = []
    context: str | None = None
    old_lines: list[str] = []
    new_lines: list[str] = []
    line_number: int | None = None
    is_end_of_file = False

    def finish_chunk() -> None:
        nonlocal context, old_lines, new_lines, line_number, is_end_of_file
        if line_number is None:
            return
        if not old_lines and not new_lines:
            _format_error(
                "Update hunk does not contain any change lines",
                line_number,
                path,
            )
        chunks.append(
            UpdateFileChunk(
                context=context,
                old_lines=tuple(old_lines),
                new_lines=tuple(new_lines),
                is_end_of_file=is_end_of_file,
                line_number=line_number,
            )
        )
        context = None
        old_lines = []
        new_lines = []
        line_number = None
        is_end_of_file = False

    for child in node.children:
        if not isinstance(child, Tree):
            continue
        if child.data == "bare_context" or child.data == "context":
            finish_chunk()
            context = None if child.data == "bare_context" else _tree_token(child, "CONTEXT")
            line_number = _node_line(child)
            continue
        if child.data == "changed_line":
            if line_number is None:
                line_number = _node_line(child)
            prefix = _tree_token(child, "CHANGE_PREFIX")
            value = _tree_token(child, "CHANGE_TEXT", default="")
            if prefix == "+":
                new_lines.append(value)
            elif prefix == "-":
                old_lines.append(value)
            else:
                old_lines.append(value)
                new_lines.append(value)
            continue
        if child.data == "eof_line":
            if line_number is None or (not old_lines and not new_lines):
                _format_error(
                    "'*** End of File' must follow an @@ hunk with change lines",
                    _node_line(child),
                    path,
                )
            if is_end_of_file:
                _format_error(
                    "An update hunk cannot contain more than one '*** End of File' marker",
                    _node_line(child),
                    path,
                )
            is_end_of_file = True
            continue
    finish_chunk()
    return tuple(chunks)


def _tree_filename(node: Tree | None) -> str:
    if node is None:
        _format_error("Patch file hunk is missing a path", 1)
    filename = next(
        (
            child
            for child in node.children
            if isinstance(child, Tree) and child.data == "filename"
        ),
        None,
    )
    if filename is None:
        _format_error("Patch file hunk is missing a path", _node_line(node))
    return _tree_token(filename, "FILENAME")


def _tree_token(node: Tree, token_type: str, *, default: str | None = None) -> str:
    value = next(
        (
            str(child)
            for child in node.children
            if isinstance(child, Token) and child.type == token_type
        ),
        default,
    )
    if value is None:
        if default is not None:
            return default
        _format_error("Patch parse tree is missing a required value", _node_line(node))
    return value


def _node_line(node: Tree) -> int:
    return int(getattr(node.meta, "line", 1) or 1)


def _line_count(tree: str) -> int:
    return max(1, tree.count("\n") + 1)


def _line_count_from_tree(tree: Tree) -> int:
    return _node_line(tree)


def _raise_lark_format_error(text: str, error: UnexpectedInput) -> NoReturn:
    lines = text.split("\n")
    line_number = int(getattr(error, "line", None) or len(lines) or 1)
    first_line = lines[0].strip() if lines else ""
    if first_line != BEGIN_PATCH:
        _format_error(
            "The first line of the patch must be '*** Begin Patch'",
            1,
        )

    token = getattr(error, "token", None)
    if getattr(token, "type", None) == "$END":
        _format_error(
            "The last line of the patch must be '*** End Patch'",
            line_number,
        )

    end_line = next(
        (
            index
            for index, line in enumerate(lines, start=1)
            if line.strip() == END_PATCH
        ),
        None,
    )
    if end_line is not None and line_number > end_line:
        _format_error(
            "Unexpected content after '*** End Patch'",
            line_number,
        )

    if (
        end_line is not None
        and line_number == end_line
        and not any(
            line.strip().startswith(marker)
            for line in lines[1 : end_line - 1]
            for marker in (ADD_FILE, UPDATE_FILE, DELETE_FILE)
        )
    ):
        _format_error("Patch must contain at least one file hunk", end_line)

    target_path, marker = _target_before_line(lines, line_number)
    if marker == ADD_FILE:
        message = (
            "Add File content must start with '+'; the next file hunk or "
            "End Patch may follow after the content"
        )
    elif marker == UPDATE_FILE:
        current_line = (
            lines[line_number - 1].strip() if line_number <= len(lines) else ""
        )
        if current_line.startswith(MOVE_TO):
            message = "Move to must appear immediately after Update File and only once"
        elif current_line == END_OF_FILE:
            if any(line.strip() == END_OF_FILE for line in lines[: line_number - 1]):
                message = (
                    "An update hunk cannot contain more than one "
                    "'*** End of File' marker"
                )
            else:
                message = "'*** End of File' must follow an @@ hunk with change lines"
        else:
            message = (
                "Invalid update hunk line; every change line must start with ' ', '+' or '-'"
            )
    elif marker == DELETE_FILE:
        message = "Delete File must not contain content"
    else:
        message = (
            "Expected a file hunk header ('*** Add File: path', "
            "'*** Update File: path', or '*** Delete File: path') "
            "or '*** End Patch'"
        )
    _format_error(message, line_number, target_path)


def _target_before_line(
    lines: list[str], line_number: int
) -> tuple[str | None, str | None]:
    for line in reversed(lines[: max(0, line_number - 1)]):
        stripped = line.strip()
        for marker in (ADD_FILE, UPDATE_FILE, DELETE_FILE):
            if stripped.startswith(marker):
                path = stripped[len(marker) :].strip()
                return (path or None), marker
    return None, None


def _encoder_path(change: object, field_name: str) -> str:
    value = getattr(change, field_name, None)
    if not isinstance(value, str):
        raise PatchError("patch_format_error", f"{field_name} must be text")
    value = _validate_encoder_line(value, field_name)
    if not value:
        raise PatchError("patch_format_error", f"{field_name} must be non-empty")
    return value


def _encoder_text(change: object, field_name: str) -> str:
    value = getattr(change, field_name, None)
    if not isinstance(value, str):
        raise PatchError("patch_format_error", f"{field_name} must be text")
    return value


def _validate_encoder_line(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise PatchError("patch_format_error", f"{field_name} must be text")
    if any(character in value for character in {"\x00", "\n", "\r"}):
        raise PatchError(
            "patch_format_error",
            f"{field_name} must contain a single line",
        )
    return value


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


def _format_error(
    message: str,
    line_number: int,
    target_path: str | None = None,
) -> NoReturn:
    raise PatchError(
        "patch_format_error",
        message,
        line_number=line_number,
        target_path=target_path,
    ) from None


__all__ = [
    "AddFile",
    "DeleteFile",
    "PatchError",
    "UpdateFile",
    "UpdateFileChunk",
    "apply_update",
    "encode_patch",
    "patch_grammar",
    "parse_patch",
]
