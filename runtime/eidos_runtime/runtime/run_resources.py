from __future__ import annotations

import re

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.extensions.mcp import McpManager
from eidos_runtime.extensions.plugins import PluginCatalog
from eidos_runtime.extensions.skills import SkillCatalog, SkillReadError
from eidos_runtime.runtime.tool_dispatcher import ToolDispatcher
from eidos_runtime.runtime.resource_registry import ResourceRegistry
from eidos_runtime.tools.registry import ToolRegistry, ToolRegistryEntry
from eidos_runtime.tools.search import tool_search_entry
from eidos_runtime.tools.workspace import ToolExecutor


class RunResourceError(RuntimeError):
    pass


class RunResources:
    """Owns every Run-scoped tool and extension resource."""

    def __init__(
        self,
        store: SessionStore,
        run_id: str,
        extension_snapshot: dict[str, object],
        user_input: str = "",
        *,
        mcp_sandbox: bool = True,
        resource_registry: ResourceRegistry | None = None,
    ) -> None:
        self.store = store
        self.run_id = run_id
        self.extension_snapshot = extension_snapshot
        self.user_input = user_input
        self.mcp_sandbox = mcp_sandbox
        self.resources = resource_registry or ResourceRegistry()
        self.tool_executor: ToolExecutor | None = None
        self.skills: SkillCatalog | None = None
        self.mcp: McpManager | None = None
        self.registry: ToolRegistry | None = None
        self.dispatcher: ToolDispatcher | None = None
        self.skill_context: tuple[dict[str, object], ...] = ()
        self._closed = False

    def __enter__(self) -> "RunResources":
        try:
            workspace = self.store.workspace_for_run(self.run_id)
            self.tool_executor = ToolExecutor(workspace)
            self.skills = SkillCatalog(PluginCatalog(self.store))
            self.mcp = McpManager(
                self.skills.plugins,
                self.extension_snapshot,
                workspace.path,
                sandbox=self.mcp_sandbox,
                resource_registry=self.resources,
            )
            self._set_registry(self.mcp.start())
            self._activate_mentions(self.user_input)
            self.skill_context = self.skills.context(
                self.extension_snapshot, self.user_input
            )
            return self
        except SkillReadError:
            self.close()
            raise RunResourceError("SKILL_SNAPSHOT_INVALID") from None
        except Exception:
            self.close()
            raise

    def refresh(self, new_inputs: tuple[str, ...] = ()) -> None:
        if self.mcp is None:
            raise RuntimeError("run resources are not started")
        try:
            entries = self.mcp.refresh_if_changed()
            if entries is not None:
                self._set_registry(entries)
            if new_inputs:
                added = "\n".join(new_inputs)
                self.user_input = f"{self.user_input}\n{added}"
                self._activate_mentions(added)
                assert self.skills is not None
                self.skill_context = self.skills.context(
                    self.extension_snapshot, self.user_input
                )
        except SkillReadError:
            raise RunResourceError("SKILL_SNAPSHOT_INVALID") from None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.mcp is not None:
            self.mcp.close()
        if self.tool_executor is not None:
            self.tool_executor.close()

    def __exit__(self, *_error: object) -> None:
        self.close()

    def _set_registry(
        self, external_entries: tuple[ToolRegistryEntry, ...]
    ) -> None:
        if self.tool_executor is None or self.skills is None:
            raise RuntimeError("run resources are not started")
        base = ToolRegistry.build(
            builtin_entries=(
                *self.tool_executor.registry.entries,
                *self.skills.tool_entries(self.extension_snapshot),
            ),
            external_entries=external_entries,
        )
        deferred = tuple(
            entry for entry in base.entries if entry.spec.visibility == "deferred"
        )
        self.registry = ToolRegistry((*base.entries, tool_search_entry(deferred)))
        self.dispatcher = ToolDispatcher(self.registry)

    def _activate_mentions(self, user_input: str) -> None:
        if self.registry is None:
            return
        mentioned_plugins = {
            match.group(1)
            for match in re.finditer(
                r"@([a-z][a-z0-9_-]{0,63})(?::[A-Za-z0-9_-]{1,64})?",
                user_input,
            )
        }
        mentioned_tools = tuple(
            entry.spec.name
            for entry in self.registry.entries
            if entry.spec.visibility == "deferred"
            and entry.provenance.plugin_id in mentioned_plugins
        )
        if mentioned_tools:
            self.store.activate_tools(self.run_id, mentioned_tools)
