"""Advisory syntax check for the bundled Tree-sitter grammars.

A grammar can trail its language, so a reported error is a hint for the model
to look at the line. It is never proof that the file is invalid.
"""

from __future__ import annotations

from typing import Literal

from tree_sitter import Node, Parser

from eidos_runtime.models import EidosFrozenStrictModel
from eidos_runtime.repo_intelligence.index import _LANGUAGE_FACTORIES, _language_for
from eidos_runtime.repo_intelligence.inventory import _language_for as _language_for_path

MAX_SYNTAX_SOURCE_BYTES = 1024 * 1024
MAX_SYNTAX_ISSUES = 8
_EXCERPT_CHARS = 80


class SyntaxIssue(EidosFrozenStrictModel):
    line: int
    column: int
    kind: Literal["error", "missing"]
    message: str


def syntax_errors(
    path: str, source: str, *, limit: int = MAX_SYNTAX_ISSUES,
) -> tuple[SyntaxIssue, ...] | None:
    """Return parse errors in ``source``, or None when the file is not checked.

    Unsupported languages and oversized files are not checked. Positions are
    1-based and count characters.
    """

    language = _language_for_path(path)
    if language not in _LANGUAGE_FACTORIES or limit <= 0:
        return None
    encoded = source.encode("utf-8")
    if len(encoded) > MAX_SYNTAX_SOURCE_BYTES:
        return None
    # A Parser is not safe to share with the indexing threads; the Language is.
    root = Parser(_language_for(language)).parse(encoded).root_node
    if not root.has_error:
        return ()
    lines = encoded.split(b"\n")
    issues: list[SyntaxIssue] = []
    reported_rows: set[int] = set()
    pending: list[Node] = [root]
    while pending and len(issues) < limit:
        node = pending.pop()
        if node.is_missing or node.is_error:
            # One hint per line: several error nodes there describe one mistake.
            if node.start_point[0] not in reported_rows:
                reported_rows.add(node.start_point[0])
                issues.append(_issue(node, lines))
            continue
        # Visit in source order: the stack takes the children reversed.
        pending.extend(child for child in reversed(node.children) if child.has_error or child.is_missing)
    return tuple(issues)


def _issue(node: Node, lines: list[bytes]) -> SyntaxIssue:
    row, byte_column = node.start_point
    line = lines[row] if row < len(lines) else b""
    column = len(line[:byte_column].decode("utf-8", errors="ignore")) + 1
    if node.is_missing:
        return SyntaxIssue(
            line=row + 1, column=column, kind="missing",
            message=f"missing `{node.type}`",
        )
    excerpt = line.decode("utf-8", errors="ignore").strip()[:_EXCERPT_CHARS]
    return SyntaxIssue(
        line=row + 1, column=column, kind="error",
        message=f"unexpected syntax near `{excerpt}`" if excerpt else "unexpected syntax",
    )
