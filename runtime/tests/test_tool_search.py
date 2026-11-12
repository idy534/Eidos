"""Which connected tools a Run lists only after a search, and how tool_search finds them."""
from __future__ import annotations

import threading

from eidos_runtime.model.client import ModelResponse, ModelToolCall
from eidos_runtime.runtime.tool_dispatcher import ToolDispatcher
from eidos_runtime.tools.registry import (
    MAX_ACTIVATED_SCHEMA_BYTES,
    MAX_ACTIVATED_TOOLS,
    MAX_SINGLE_DEFINITION_BYTES,
    PreparedToolInvocation,
    ToolProvenance,
    ToolRegistry,
    ToolRegistryEntry,
    ToolSpec,
)
from eidos_runtime.tools.search import (
    MAX_DESCRIBED_SOURCES,
    TOOL_SEARCH_NAME,
    deferred_tool_names,
    tool_search_entry,
)

RESULT_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}


class _Adapter:
    def execute(self, arguments: dict[str, object], _cancel: threading.Event) -> dict[str, object]:
        raise AssertionError("fixture tools are never executed here")


def _entry(name: str, *, kind: str, description: str, plugin: str | None = None, server: str | None = None) -> ToolRegistryEntry:
    return ToolRegistryEntry(
        spec=ToolSpec.model_validate({
            "name": name,
            "description": description,
            "sideEffect": "external" if kind == "mcp" else "none",
            "approvalRequired": kind == "mcp",
            "timeoutSeconds": 5,
            "batchPolicy": "single",
            "visibility": "direct",
            "inputSchema": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            "resultSchema": RESULT_SCHEMA,
        }),
        provenance=ToolProvenance.model_validate({
            "kind": kind,
            "sourceId": server or "test",
            "sourceVersion": "1",
            "contentHash": "a" * 64,
            **({"pluginId": plugin} if plugin else {}),
            **({"serverId": server} if server else {}),
        }),
        adapter=_Adapter(),
    )


def mcp_tool(server: str, tool: str, description: str = "Connected tool.", *, plugin: str = "acme") -> ToolRegistryEntry:
    return _entry(f"mcp__{server}__{tool}", kind="mcp", description=description, plugin=plugin, server=server)


def builtin_tool(name: str = "read_notes") -> ToolRegistryEntry:
    return _entry(name, kind="builtin", description="Built-in tool.")


def many_tools(count: int, server: str = "tracker") -> tuple[ToolRegistryEntry, ...]:
    return tuple(mcp_tool(server, f"tool_{index:02}") for index in range(count))


def run_registry(entries: tuple[ToolRegistryEntry, ...]) -> ToolRegistry:
    """Build the registry the way a Run does once some connected tools are unlisted."""
    deferred = deferred_tool_names(entries)
    searchable = tuple(entry for entry in entries if entry.spec.name in deferred)
    return ToolRegistry((*entries, tool_search_entry(searchable)), deferred_names=deferred)


def search(entry: ToolRegistryEntry, query: str, limit: int = 10, cancel: threading.Event | None = None):
    cancel = cancel or threading.Event()
    prepared = PreparedToolInvocation({"query": query, "limit": limit})
    raw = entry.runtime.execute(None, prepared, cancel)
    return entry.runtime.verify(None, prepared, raw, cancel)


# --- which tools are unlisted -----------------------------------------------------


def test_connected_tools_within_the_activation_budget_stay_listed():
    entries = (builtin_tool(), *many_tools(MAX_ACTIVATED_TOOLS))

    assert deferred_tool_names(entries) == frozenset()


def test_one_tool_past_the_count_budget_unlists_every_connected_tool_but_no_builtin():
    connected = many_tools(MAX_ACTIVATED_TOOLS + 1)

    deferred = deferred_tool_names((builtin_tool(), *connected))

    assert deferred == {entry.spec.name for entry in connected}


def test_a_few_tools_with_large_definitions_are_unlisted_by_size():
    # Each definition is under the single-tool limit and takes about a fifth of the byte budget.
    padding = "x" * (MAX_ACTIVATED_SCHEMA_BYTES // 5)
    assert len(padding) < MAX_SINGLE_DEFINITION_BYTES
    heavy = tuple(mcp_tool("docs", f"tool_{index}", description=padding) for index in range(6))

    assert deferred_tool_names(heavy[:4]) == frozenset()
    assert deferred_tool_names(heavy) == {entry.spec.name for entry in heavy}


def test_a_tool_too_large_to_activate_stays_listed_and_does_not_count():
    oversized = mcp_tool("docs", "huge", description="x" * (MAX_SINGLE_DEFINITION_BYTES + 1))
    connected = many_tools(MAX_ACTIVATED_TOOLS + 1)

    # Alone it is never unlisted: an unlisted tool this size could not be activated again.
    assert deferred_tool_names((oversized, *many_tools(3))) == frozenset()
    assert deferred_tool_names((oversized, *connected)) == {entry.spec.name for entry in connected}


def test_always_listed_tools_neither_count_toward_the_budget_nor_get_unlisted():
    browser = many_tools(MAX_ACTIVATED_TOOLS + 7, server="browser")
    other = many_tools(3, server="tracker")
    is_browser = lambda entry: entry.provenance.server_id == "browser"  # noqa: E731

    # The 23 browser tools alone do not push the three tracker tools over the budget.
    assert deferred_tool_names((*browser, *other), always_listed=is_browser) == frozenset()

    crowded = many_tools(MAX_ACTIVATED_TOOLS + 1, server="tracker")
    assert deferred_tool_names((*browser, *crowded), always_listed=is_browser) == {
        entry.spec.name for entry in crowded
    }


# --- what the model sees ----------------------------------------------------------


def test_unlisted_tools_are_absent_from_definitions_until_a_search_activates_them():
    connected = many_tools(MAX_ACTIVATED_TOOLS + 1)
    registry = run_registry((builtin_tool(), *connected))
    wanted = connected[5].spec.name

    listed = {definition.name for definition in registry.model_definitions()}
    assert listed == {"read_notes", TOOL_SEARCH_NAME}

    snapshot = registry.snapshot(activated_names=(wanted,))
    assert set(snapshot.available_names) == {"read_notes", TOOL_SEARCH_NAME, wanted}
    assert set(snapshot.deferred_names) == {entry.spec.name for entry in connected}
    assert {definition.name for definition in registry.model_definitions((wanted,))} == {
        "read_notes", TOOL_SEARCH_NAME, wanted,
    }


def test_activation_is_capped_so_searching_everything_cannot_list_everything():
    connected = many_tools(MAX_ACTIVATED_TOOLS + 9)
    registry = run_registry(connected)

    snapshot = registry.snapshot(activated_names=tuple(entry.spec.name for entry in connected))

    assert len(snapshot.activated_names) == MAX_ACTIVATED_TOOLS


def test_the_description_names_unlisted_sources_and_stays_bounded():
    entries = (
        *many_tools(MAX_ACTIVATED_TOOLS, server="tracker"),
        *many_tools(2, server="wiki"),
    )
    description = tool_search_entry(entries).spec.description

    assert f"tracker ({MAX_ACTIVATED_TOOLS} tools), wiki (2 tools)" in description
    assert "tool_not_activated" in description

    sources = tuple(mcp_tool(f"server{index:02}", "only") for index in range(MAX_DESCRIBED_SOURCES + 3))
    crowded = tool_search_entry(sources).spec.description
    assert "and 3 more sources" in crowded
    assert "server14" not in crowded


def test_a_call_to_an_unlisted_tool_is_kept_for_feedback_instead_of_rejected_as_unknown():
    connected = many_tools(MAX_ACTIVATED_TOOLS + 1)
    registry = run_registry(connected)
    dispatcher = ToolDispatcher(registry)
    snapshot = registry.snapshot()
    unlisted = connected[0].spec.name

    validated = dispatcher.validate(
        ModelResponse(tool_calls=(ModelToolCall("call-1", unlisted, {"value": "x"}),)),
        snapshot.available_names,
        snapshot.tool_set_hash,
    )

    # The Runtime answers this call with tool_not_activated; it is not a protocol error.
    assert validated.error_code is None
    assert [call.name for call in validated.tool_calls] == [unlisted]
    assert dispatcher.has_tool(TOOL_SEARCH_NAME)


# --- searching --------------------------------------------------------------------


def test_an_exact_name_ranks_first_and_every_hit_becomes_callable():
    entries = (
        mcp_tool("tracker", "create_issue", "Open a new issue in the tracker."),
        mcp_tool("tracker", "create_issue_comment", "Comment on an issue."),
        mcp_tool("wiki", "search_pages", "Find wiki pages."),
    )

    output = search(tool_search_entry(entries), "mcp__tracker__create_issue")

    hits = output.result["data"]["hits"]
    assert [hit["name"] for hit in hits] == ["mcp__tracker__create_issue", "mcp__tracker__create_issue_comment"]
    assert hits[0]["score"] > hits[1]["score"]
    assert hits[0]["provenance"]["serverId"] == "tracker"
    assert output.activated_tool_names == ("mcp__tracker__create_issue", "mcp__tracker__create_issue_comment")
    assert output.result["outcome"] == "success"


def test_keywords_match_descriptions_and_sources_case_insensitively():
    entries = (
        mcp_tool("tracker", "create_issue", "Open a new issue in the tracker."),
        mcp_tool("wiki", "search_pages", "Find WIKI pages."),
    )
    entry = tool_search_entry(entries)

    assert [hit["name"] for hit in search(entry, "wiki pages").result["data"]["hits"]] == ["mcp__wiki__search_pages"]
    assert [hit["name"] for hit in search(entry, "acme").result["data"]["hits"]] == [
        "mcp__tracker__create_issue", "mcp__wiki__search_pages",
    ]


def test_the_limit_truncates_hits_and_only_returned_hits_are_activated():
    entry = tool_search_entry(many_tools(8))

    output = search(entry, "tracker", limit=3)

    data = output.result["data"]
    assert len(data["hits"]) == 3
    assert (data["totalMatches"], data["truncated"], data["truncationReason"]) == (8, True, "limit")
    assert output.activated_tool_names == tuple(hit["name"] for hit in data["hits"])


def test_no_match_is_a_successful_empty_result_that_activates_nothing():
    output = search(tool_search_entry(many_tools(4)), "calendar")

    assert output.result["outcome"] == "success"
    assert output.result["data"]["hits"] == []
    assert output.result["data"]["totalMatches"] == 0
    assert output.activated_tool_names == ()


def test_a_canceled_search_reports_interruption_and_activates_nothing():
    cancel = threading.Event()
    cancel.set()

    output = search(tool_search_entry(many_tools(4)), "tracker", cancel=cancel)

    assert (output.result["outcome"], output.result["code"]) == ("interrupted", "tool_canceled")
    assert output.activated_tool_names == ()


def test_search_results_validate_against_the_registered_contract():
    entry = tool_search_entry(many_tools(4))
    output = search(entry, "tracker", limit=2)

    registry = ToolRegistry((entry,))
    validated = registry.get(TOOL_SEARCH_NAME).validate_arguments({"query": "  tracker  "})

    assert validated.valid
    assert validated.normalized_arguments == {"query": "tracker", "limit": 10}
    assert entry.validate_result(output.result)["data"]["totalMatches"] == 4
