from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import threading
from typing import ClassVar, Literal

from pydantic import Field, StrictInt, StrictStr

from eidos_runtime.db.database import Database
from eidos_runtime.persistence.history_search import HistorySearchRepository
from eidos_runtime.tools.contracts import StrictToolModel, result_model
from eidos_runtime.tools.registry import ToolProvenance, ToolRegistryEntry, ToolSpec


class SearchHistoryInput(StrictToolModel):
    query: StrictStr = Field(min_length=2, max_length=200)
    limit: StrictInt = Field(default=8, ge=1, le=20)


class SearchHistoryMatchData(StrictToolModel):
    itemId: StrictStr
    kind: Literal["user", "assistant", "tool"]
    snippet: StrictStr
    readCallId: StrictStr
    stream: Literal["context", "result"]
    toolName: StrictStr | None = None


class SearchHistoryResultData(StrictToolModel):
    SUCCESS_REQUIRED: ClassVar[tuple[str, ...]] = ("query", "matches", "complete")
    query: StrictStr | None = None
    matches: tuple[SearchHistoryMatchData, ...] | None = None
    complete: bool | None = None


@dataclass(frozen=True)
class SearchHistoryAdapter:
    database: Database
    run_id: str

    def execute(self, arguments: dict[str, object], cancel: threading.Event) -> dict[str, object]:
        if cancel.is_set():
            return _error("tool_canceled", "History search was canceled")
        try:
            request = SearchHistoryInput.model_validate(arguments)
            found = HistorySearchRepository(self.database).search(self.run_id, request.query, limit=request.limit)
        except (TypeError, ValueError):
            return _error("invalid_arguments", "Invalid history search arguments")
        except Exception:
            return _error("history_unavailable", "The saved Session history could not be searched")
        if cancel.is_set():
            return _error("tool_canceled", "History search was canceled")
        return {
            "schemaVersion": 1,
            "toolContractVersion": 1,
            "toolName": "search_history",
            "outcome": "success",
            "code": "ok",
            "summary": f"Found {len(found.matches)} saved history match(es)",
            "data": {
                "query": request.query,
                "matches": [{
                    "itemId": match.item_id,
                    "kind": match.kind,
                    "snippet": match.snippet,
                    # Messages are paged by item ID, tool calls by provider callId.
                    "readCallId": match.call_id or match.item_id,
                    "stream": "result" if match.kind == "tool" else "context",
                    **({"toolName": match.tool_name} if match.tool_name else {}),
                } for match in found.matches],
                "complete": found.complete,
            },
            "sideEffectsMayExist": False,
            "reconciliationRequired": False,
        }


def search_history_entry(database: Database, run_id: str) -> ToolRegistryEntry:
    input_schema = SearchHistoryInput.model_json_schema(by_alias=True)
    result_schema = result_model(SearchHistoryResultData).model_json_schema(by_alias=True)
    encoded = json.dumps((input_schema, result_schema), ensure_ascii=False, separators=(",", ":"),
                         sort_keys=True).encode("utf-8")
    spec = ToolSpec.model_validate({
        "name": "search_history",
        "description": (
            "Search this Session's saved history by literal, case-insensitive text: user and assistant "
            "messages and earlier tool calls (arguments and results), including parts that were summarized "
            "or cleared from active context. Returns the newest matches with short snippets. Page an original "
            "with read_tool_output(callId=readCallId, itemId=itemId, stream=stream). complete=false means more "
            "matches may exist; refine the query. Searching never repeats an operation, and a saved result "
            "does not prove the current state of a file. Memory tool payloads are not searched."
        ),
        "sideEffect": "none",
        "approvalRequired": False,
        "timeoutSeconds": 10,
        "batchPolicy": "parallel",
        "visibility": "direct",
        "inputSchema": input_schema,
        "resultSchema": result_schema,
        "modelProjectionPolicy": "generic",
        "contractVersion": 1,
    })
    return ToolRegistryEntry(
        spec=spec,
        provenance=ToolProvenance.model_validate({
            "kind": "builtin",
            "sourceId": "eidos.search-history",
            "sourceVersion": "1",
            "contentHash": hashlib.sha256(encoded).hexdigest(),
        }),
        adapter=SearchHistoryAdapter(database, run_id),
        input_model=SearchHistoryInput,
        result_data_model=SearchHistoryResultData,
    )


def _error(code: str, summary: str) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "toolContractVersion": 1,
        "toolName": "search_history",
        "outcome": "error",
        "code": code,
        "summary": summary,
        "data": {},
        "sideEffectsMayExist": False,
        "reconciliationRequired": False,
    }


__all__ = ["SearchHistoryAdapter", "SearchHistoryInput", "SearchHistoryResultData", "search_history_entry"]
