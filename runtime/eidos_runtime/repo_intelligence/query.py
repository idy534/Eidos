from __future__ import annotations

import json
import re
from collections.abc import Iterable

from eidos_runtime.context.facts import ContextFacts, ContextItemFact
from eidos_runtime.repo_intelligence.index import RepositoryIndexSnapshot
from eidos_runtime.repo_intelligence.inventory import RepositoryInventory
from eidos_runtime.repo_intelligence.retrieval import (
    MAX_QUERY_BYTES,
    RepositoryRetrievalQuery,
)


_IDENTIFIER = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_]*(?![A-Za-z0-9_])")
_READ_TOOLS = frozenset({"read_file", "read_file_range"})
_PATH_RESULT_TOOLS = frozenset({
    "list_files", "read_file", "read_file_range", "search_text"
})


class RepositoryTaskQueryBuilder:
    """Build one grounded retrieval query from the Run task and durable facts."""

    def build(
        self,
        text: str,
        *,
        inventory: RepositoryInventory,
        index: RepositoryIndexSnapshot,
        facts: ContextFacts,
        dirty_paths: tuple[str, ...] = (),
    ) -> RepositoryRetrievalQuery:
        paths = {record.path for record in inventory.files}
        mentioned_paths = tuple(sorted(
            (
                path for path in paths
                if _mentions_path(text, path)
            ),
            key=str.encode,
        ))
        task_identifiers = set(_IDENTIFIER.findall(text))
        symbols = {symbol.name for symbol in index.symbols}
        mentioned_symbols = tuple(sorted(task_identifiers & symbols))
        recent_items = facts.items[-64:]
        previous_read_paths = _grounded_paths(
            (
                path
                for item in recent_items
                if item.tool_name in _READ_TOOLS
                for path in _argument_paths(item)
            ),
            paths,
        )
        recent_tool_result_paths = _grounded_paths(
            (
                path
                for item in recent_items
                if item.tool_name in _PATH_RESULT_TOOLS
                for path in _result_paths(item)
            ),
            paths,
        )
        recently_modified_paths = _grounded_paths(
            (*facts.committed_workspace_changes, *dirty_paths), paths
        )
        return RepositoryRetrievalQuery(
            text=_utf8_prefix(text, MAX_QUERY_BYTES),
            mentioned_paths=mentioned_paths,
            mentioned_symbols=mentioned_symbols,
            recently_modified_paths=recently_modified_paths,
            previous_read_paths=previous_read_paths,
            recent_tool_result_paths=recent_tool_result_paths,
        )


def _mentions_path(text: str, path: str) -> bool:
    normalized = text.replace("\\", "/")
    basename = path.rsplit("/", 1)[-1]
    return path in normalized or bool(re.search(
        rf"(?<![A-Za-z0-9_.-]){re.escape(basename)}(?![A-Za-z0-9_.-])",
        normalized,
    ))


def _argument_paths(item: ContextItemFact) -> tuple[str, ...]:
    return _paths_from_json(item.arguments_json)


def _result_paths(item: ContextItemFact) -> tuple[str, ...]:
    return _paths_from_json(item.model_result_json or item.result_json)


def _paths_from_json(raw: str | None) -> tuple[str, ...]:
    if raw is None:
        return ()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    found: list[str] = []

    def visit(node: object, key: str | None = None) -> None:
        if isinstance(node, dict):
            for child_key, child in node.items():
                if child_key in {"path", "paths"}:
                    visit(child, child_key)
                elif child_key in {"data", "matches", "results", "files"}:
                    visit(child, child_key)
        elif isinstance(node, list):
            for child in node:
                visit(child, key)
        elif isinstance(node, str) and key in {"path", "paths", "files"}:
            found.append(node.replace("\\", "/"))

    visit(value)
    return tuple(found)


def _grounded_paths(
    values: Iterable[str], inventory_paths: set[str]
) -> tuple[str, ...]:
    return tuple(sorted(
        {value for value in values if isinstance(value, str) and value in inventory_paths},
        key=str.encode,
    ))


def _utf8_prefix(value: str, byte_limit: int) -> str:
    prefix = value.encode("utf-8")[:byte_limit]
    while True:
        try:
            return prefix.decode("utf-8")
        except UnicodeDecodeError as error:
            prefix = prefix[:error.start]


__all__ = ["RepositoryTaskQueryBuilder"]
