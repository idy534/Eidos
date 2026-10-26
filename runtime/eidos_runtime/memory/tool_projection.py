"""Compact model views; durable memory records retain their complete metadata."""

from __future__ import annotations

from typing import Literal

from pydantic import JsonValue

from eidos_runtime.memory.contracts import (
    GLOBAL_SCOPE_ID,
    MemoryActionResult,
    MemoryDocument,
    MemoryKind,
    MemoryReadResult,
    NonnegativeInt,
)
from eidos_runtime.models import EidosFrozenStrictModel

MEMORY_TOOL_NAMES = frozenset({"memory_read", "memory_search", "memory_edit"})


class MemoryModelNote(EidosFrozenStrictModel):
    scope: Literal["current", "global"]
    path: str
    revision: NonnegativeInt
    summary: str
    archived: Literal[True] | None = None


class MemoryModelRead(EidosFrozenStrictModel):
    scope: Literal["current", "global"]
    path: str
    revision: NonnegativeInt
    # A full-note replacement also writes these user-visible fields.
    title: str | None = None
    summary: str | None = None
    kind: MemoryKind | None = None
    archived: Literal[True] | None = None
    content: str
    next_offset: NonnegativeInt | None = None


class MemoryModelSearch(EidosFrozenStrictModel):
    documents: list[MemoryModelNote]
    next_cursor: NonnegativeInt | None = None
    search_limited: Literal[True] | None = None


class MemoryModelEdit(EidosFrozenStrictModel):
    path: str
    revision: NonnegativeInt | None = None


class MemoryModelResult(EidosFrozenStrictModel):
    outcome: str
    code: str | None = None
    summary: str | None = None
    data: MemoryModelRead | MemoryModelSearch | MemoryModelEdit | None = None
    side_effects_may_exist: Literal[True] | None = None
    reconciliation_required: Literal[True] | None = None


def _scope(identifier: str) -> Literal["current", "global"]:
    return "global" if identifier == GLOBAL_SCOPE_ID else "current"


def _serialized_bytes(result: MemoryModelResult) -> int:
    return len(result.model_dump_json(by_alias=True, exclude_none=True).encode("utf-8"))


def _fit_read(
    result: MemoryModelResult, offset: int, max_bytes: int
) -> MemoryModelResult:
    if _serialized_bytes(result) <= max_bytes:
        return result
    page = result.data
    if not isinstance(page, MemoryModelRead):
        raise ValueError("memory_projection_budget_invalid")
    # Choose a complete character prefix, including JSON escaping in the budget.
    # The next read starts at the first omitted character, rather than skipping
    # the remainder of a page which the generic projector truncated.
    lower, upper = 0, len(page.content)
    while lower < upper:
        middle = (lower + upper + 1) // 2
        candidate = result.model_copy(
            update={
                "data": page.model_copy(
                    update={
                        "content": page.content[:middle],
                        "next_offset": offset + middle,
                    }
                )
            }
        )
        if _serialized_bytes(candidate) <= max_bytes:
            lower = middle
        else:
            upper = middle - 1
    if lower == 0:
        raise ValueError("memory_projection_budget_invalid")
    return result.model_copy(
        update={
            "data": page.model_copy(
                update={
                    "content": page.content[:lower],
                    "next_offset": offset + lower,
                }
            )
        }
    )


def project_memory_tool_result(
    tool_name: str, value: object, *, max_bytes: int = 48 * 1024
) -> dict[str, JsonValue]:
    """Project either a canonical result or an already compact model result."""
    if tool_name not in MEMORY_TOOL_NAMES or not isinstance(value, dict):
        raise ValueError("memory_result_invalid")
    outcome = value.get("outcome")
    if not isinstance(outcome, str):
        raise ValueError("memory_result_invalid")
    result = MemoryModelResult(
        outcome=outcome,
        side_effects_may_exist=True
        if value.get("sideEffectsMayExist") is True
        else None,
        reconciliation_required=True
        if value.get("reconciliationRequired") is True
        else None,
    )
    if outcome != "success":
        result = result.model_copy(
            update={
                "code": str(value.get("code", "memory_request_failed"))[:128],
                "summary": str(value.get("summary", "Memory request was not applied."))[
                    :2048
                ],
            }
        )
        if _serialized_bytes(result) > max_bytes:
            raise ValueError("memory_projection_budget_invalid")
        return result.model_dump(mode="json", by_alias=True, exclude_none=True)
    data = value.get("data")
    if not isinstance(data, dict):
        raise ValueError("memory_result_invalid")
    offset = 0
    if tool_name == "memory_read":
        if isinstance(data.get("read"), dict):
            read = MemoryReadResult.model_validate(data["read"])
            page = MemoryModelRead(
                scope=_scope(read.scope_id),
                path=read.path,
                revision=read.revision,
                title=read.document.title if read.document else None,
                summary=read.document.summary if read.document else None,
                kind=read.document.kind if read.document else None,
                archived=True if read.document and read.document.archived else None,
                content=read.content,
                next_offset=read.next_offset,
            )
            offset = read.offset
        else:
            page = MemoryModelRead.model_validate(
                {key: value for key, value in data.items() if key != "userOwned"}
            )
        result = result.model_copy(update={"data": page})
        if isinstance(data.get("read"), dict):
            result = _fit_read(result, offset, max_bytes)
    elif tool_name == "memory_search":
        documents = data.get("documents")
        if not isinstance(documents, list):
            raise ValueError("memory_result_invalid")
        notes = []
        for raw in documents:
            if isinstance(raw, dict) and ("scopeId" in raw or "scope_id" in raw):
                note = MemoryDocument.model_validate(raw)
                notes.append(
                    MemoryModelNote(
                        scope=_scope(note.scope_id),
                        path=note.path,
                        revision=note.revision,
                        summary=note.summary,
                        archived=True if note.archived else None,
                    )
                )
            else:
                notes.append(MemoryModelNote.model_validate(raw))
        result = result.model_copy(
            update={
                "data": MemoryModelSearch(
                    documents=notes,
                    next_cursor=data.get("nextCursor", data.get("next_cursor")),
                    search_limited=True
                    if data.get("searchLimited", data.get("search_limited")) is True
                    else None,
                )
            }
        )
    else:
        if isinstance(data.get("action"), dict):
            action = MemoryActionResult.model_validate(data["action"])
            edit = MemoryModelEdit(
                path=action.path,
                revision=None if action.code == "memory_deleted" else action.revision,
            )
            code = action.code
        else:
            edit = MemoryModelEdit.model_validate(data)
            code = value.get("code")
            if not isinstance(code, str) or code not in {
                "memory_saved",
                "memory_unchanged",
                "memory_deleted",
                "memory_archived",
            }:
                raise ValueError("memory_result_invalid")
        result = result.model_copy(update={"data": edit, "code": code})
    if _serialized_bytes(result) > max_bytes:
        raise ValueError("memory_projection_budget_invalid")
    return result.model_dump(mode="json", by_alias=True, exclude_none=True)
