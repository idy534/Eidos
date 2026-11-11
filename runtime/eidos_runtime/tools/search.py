"""tool_search: find connected tools that a Run does not list up front and make them callable."""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
import hashlib
import threading

from eidos_runtime.tools.contracts import (
    ToolSearchInput,
    ToolSearchResultData,
    result_model,
)
from eidos_runtime.tools.registry import (
    AdapterToolRuntime,
    PreparedToolInvocation,
    ToolProvenance,
    ToolRegistryEntry,
    ToolSpec,
    VerifiedToolOutput,
    fits_activation_budget,
)

TOOL_SEARCH_NAME = "tool_search"
# The description names the unlisted sources so the model knows what a search can find.
MAX_DESCRIBED_SOURCES = 12


def deferred_tool_names(
    entries: tuple[ToolRegistryEntry, ...],
    *,
    always_listed: Callable[[ToolRegistryEntry], bool] = lambda _entry: False,
) -> frozenset[str]:
    """Return the connected MCP tools that this Run lists only after a search.

    Connected tools stay listed while all of them fit the budget that bounds
    activated tools: deferring a set that could be activated whole saves nothing.
    Past that budget, sending every definition with every request costs more
    context than one search step, so the whole set is deferred.
    """
    candidates = tuple(
        entry for entry in entries
        if entry.provenance.kind == "mcp" and not always_listed(entry)
    )
    if not candidates or fits_activation_budget(candidates):
        return frozenset()
    return frozenset(entry.spec.name for entry in candidates)


class ToolSearchAdapter:
    def __init__(self, candidates: tuple[ToolRegistryEntry, ...]) -> None:
        self.candidates = tuple(sorted(
            candidates, key=lambda value: value.spec.name.encode("utf-8"),
        ))

    def execute(
        self, arguments: dict[str, object], cancel: threading.Event
    ) -> dict[str, object]:
        if cancel.is_set():
            return _interrupted()
        query = str(arguments["query"]).casefold()
        tokens = tuple(value for value in query.split() if value)
        ranked: list[tuple[int, bytes, ToolRegistryEntry]] = []
        for entry in self.candidates:
            provenance = entry.provenance.model_dump(mode="json", by_alias=True)
            haystack = " ".join((
                entry.spec.name,
                entry.spec.description,
                str(provenance.get("pluginId") or ""),
                str(provenance.get("serverId") or ""),
                str(provenance.get("displayTitle") or ""),
            )).casefold()
            name = entry.spec.name.casefold()
            score = (
                100 if query == name else
                80 if name.startswith(query) else
                60 if query in name else
                0
            )
            score += sum(10 for token in tokens if token in haystack)
            if score:
                ranked.append((score, entry.spec.name.encode("utf-8"), entry))
        ranked.sort(key=lambda value: (-value[0], value[1]))
        selected = ranked[:int(arguments["limit"])]
        truncated = len(ranked) > len(selected)
        return {
            "toolContractVersion": 1,
            "schemaVersion": 1,
            "toolName": TOOL_SEARCH_NAME,
            "outcome": "success",
            "code": "ok",
            "summary": "Tool search completed",
            "data": {
                "query": str(arguments["query"]),
                "hits": [{
                    "name": entry.spec.name,
                    "description": entry.spec.description,
                    "provenance": entry.provenance.model_dump(mode="json", by_alias=True),
                    "score": score,
                } for score, _name, entry in selected],
                "totalMatches": len(ranked),
                "truncated": truncated,
                "truncationReason": "limit" if truncated else None,
            },
            "sideEffectsMayExist": False,
            "reconciliationRequired": False,
        }


class ToolSearchRuntime(AdapterToolRuntime):
    def verify(
        self,
        _context: object,
        _prepared: PreparedToolInvocation,
        raw: dict[str, object],
        _cancel: threading.Event,
    ) -> VerifiedToolOutput:
        """Every hit becomes callable; the registry bounds how many stay active."""
        data = raw.get("data")
        hits = data.get("hits") if isinstance(data, dict) else None
        activated = tuple(
            str(hit["name"])
            for hit in hits if isinstance(hit, dict) and "name" in hit
        ) if isinstance(hits, list) else ()
        return VerifiedToolOutput(raw, activated_tool_names=activated)


def tool_search_entry(candidates: tuple[ToolRegistryEntry, ...]) -> ToolRegistryEntry:
    adapter = ToolSearchAdapter(candidates)
    spec = ToolSpec.model_validate({
        "name": TOOL_SEARCH_NAME,
        "description": (
            "Some connected tools are not listed because there are too many to send with every request. "
            "Search them by tool name, capability or source; the matching tools become callable from the next step. "
            "Use this when the task needs a connected tool that is not in the current tool list, and after a "
            "tool_not_activated result, searching for that exact tool name. "
            f"Unlisted sources: {_describe_sources(adapter.candidates)}."
        ),
        "sideEffect": "none",
        "approvalRequired": False,
        "timeoutSeconds": 5,
        "batchPolicy": "single",
        "visibility": "direct",
        "inputSchema": ToolSearchInput.model_json_schema(by_alias=True),
        "resultSchema": result_model(ToolSearchResultData).model_json_schema(by_alias=True),
        "modelProjectionPolicy": "tool_search",
    })
    provenance = ToolProvenance.model_validate({
        "kind": "builtin",
        "sourceId": "eidos.tool-search",
        "sourceVersion": "2",
        "contentHash": hashlib.sha256(TOOL_SEARCH_NAME.encode("utf-8")).hexdigest(),
    })
    return ToolRegistryEntry(
        spec=spec,
        provenance=provenance,
        adapter=adapter,
        input_model=ToolSearchInput,
        result_data_model=ToolSearchResultData,
        runtime=ToolSearchRuntime(adapter, spec, provenance),
    )


def _describe_sources(candidates: tuple[ToolRegistryEntry, ...]) -> str:
    counts = Counter(
        entry.provenance.server_id or entry.provenance.plugin_id or entry.provenance.source_id
        for entry in candidates
    )
    ordered = sorted(counts.items(), key=lambda value: (-value[1], value[0]))
    described = ", ".join(f"{source} ({count} tools)" for source, count in ordered[:MAX_DESCRIBED_SOURCES])
    hidden = len(ordered) - MAX_DESCRIBED_SOURCES
    return described + (f", and {hidden} more sources" if hidden > 0 else "")


def _interrupted() -> dict[str, object]:
    return {
        "toolContractVersion": 1,
        "schemaVersion": 1,
        "toolName": TOOL_SEARCH_NAME,
        "outcome": "interrupted",
        "code": "tool_canceled",
        "summary": "Tool search was interrupted",
        "data": {},
        "sideEffectsMayExist": False,
        "reconciliationRequired": False,
    }
