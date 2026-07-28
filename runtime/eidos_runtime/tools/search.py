from __future__ import annotations

import hashlib
import threading

from eidos_runtime.tools.registry import (
    ToolProvenance,
    AdapterToolRuntime,
    PreparedToolInvocation,
    ToolRegistryEntry,
    ToolSpec,
    VerifiedToolOutput,
)
from eidos_runtime.tools.contracts import (
    ToolSearchInput,
    ToolSearchResultData,
    result_model,
)


class ToolSearchAdapter:
    def __init__(self, candidates: tuple[ToolRegistryEntry, ...]) -> None:
        self.candidates = tuple(sorted(
            (value for value in candidates if value.spec.visibility == "deferred"),
            key=lambda value: value.spec.name.encode("utf-8"),
        ))

    def execute(
        self, arguments: dict[str, object], cancel: threading.Event
    ) -> dict[str, object]:
        if cancel.is_set():
            return _error("tool_canceled")
        query = str(arguments["query"]).casefold()
        tokens = tuple(value for value in query.split() if value)
        ranked: list[tuple[int, bytes, ToolRegistryEntry]] = []
        for entry in self.candidates:
            provenance = entry.provenance.model_dump(mode="json", by_alias=True)
            fields = [
                entry.spec.name,
                entry.spec.description,
                str(provenance.get("pluginId") or ""),
                str(provenance.get("serverId") or ""),
                str(provenance.get("skillId") or ""),
            ]
            haystack = " ".join(fields).casefold()
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
        limit = int(arguments["limit"])
        selected = ranked[:limit]
        return {
            "toolContractVersion": 1,
            "schemaVersion": 1,
            "toolName": "tool_search",
            "outcome": "success",
            "code": "ok",
            "summary": "Tool search completed",
            "data": {
                "hits": [{
                    "name": entry.spec.name,
                    "description": entry.spec.description,
                    "provenance": entry.provenance.model_dump(
                        mode="json", by_alias=True
                    ),
                    "score": score,
                } for score, _name, entry in selected],
                "totalMatches": len(ranked),
                "truncated": len(ranked) > len(selected),
                "truncationReason": "limit" if len(ranked) > len(selected) else None,
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
        data = raw.get("data")
        hits = data.get("hits") if isinstance(data, dict) else None
        activated = tuple(
            str(hit["name"])
            for hit in hits if isinstance(hit, dict) and "name" in hit
        ) if isinstance(hits, list) else ()
        return VerifiedToolOutput(raw, activated_tool_names=activated)


def tool_search_entry(candidates: tuple[ToolRegistryEntry, ...]) -> ToolRegistryEntry:
    name = "tool_search"
    adapter = ToolSearchAdapter(candidates)
    spec = ToolSpec.model_validate({
        "name": name,
        "description": "Search available deferred tools by capability and source",
        "sideEffect": "none",
        "approvalRequired": False,
        "timeoutSeconds": 5,
        "batchPolicy": "single",
        "visibility": "direct",
        "inputSchema": ToolSearchInput.model_json_schema(by_alias=True),
        "resultSchema": result_model(
            ToolSearchResultData
        ).model_json_schema(by_alias=True),
        "modelProjectionPolicy": "tool_search",
    })
    provenance = ToolProvenance.model_validate({
        "kind": "builtin",
        "sourceId": "eidos.tool-search",
        "sourceVersion": "1",
        "contentHash": hashlib.sha256(name.encode("utf-8")).hexdigest(),
    })
    return ToolRegistryEntry(
        spec=spec,
        provenance=provenance,
        adapter=adapter,
        input_model=ToolSearchInput,
        result_data_model=ToolSearchResultData,
        runtime=ToolSearchRuntime(adapter, spec, provenance),
    )


def _error(code: str) -> dict[str, object]:
    return {
        "toolContractVersion": 1,
        "schemaVersion": 1,
        "toolName": "tool_search",
        "outcome": "interrupted",
        "code": code,
        "summary": "Tool search was interrupted",
        "data": {},
        "sideEffectsMayExist": False,
        "reconciliationRequired": False,
    }
