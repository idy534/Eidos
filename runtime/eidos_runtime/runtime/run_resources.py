from __future__ import annotations

import hashlib
import re

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.extensions.mcp import McpManager
from eidos_runtime.extensions.plugins import PluginCatalog
from eidos_runtime.extensions.skill_access import (
    SkillAccess,
    SkillAccessError,
    reset_current_skill_access,
    set_current_skill_access,
)
from eidos_runtime.extensions.skills import (
    RetainedContextSection,
    SkillCatalog,
    SkillCatalogSnapshot,
    SkillReadError,
)
from eidos_runtime.runtime.tool_dispatcher import ToolDispatcher
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
from eidos_runtime.runtime.resource_registry import ResourceRegistry
from eidos_runtime.tools.registry import ToolRegistry, ToolRegistryEntry
from eidos_runtime.tools.search import tool_search_entry
from eidos_runtime.tools.runtime_workspace import ToolExecutor


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
        async_kernel: RuntimeAsyncKernel | None = None,
        mcp_sandbox: bool = True,
        resource_registry: ResourceRegistry | None = None,
    ) -> None:
        self.store = store
        self.run_id = run_id
        self.extension_snapshot = extension_snapshot
        self.user_input = user_input
        self.mcp_sandbox = mcp_sandbox
        self.async_kernel = async_kernel
        self.resources = resource_registry or ResourceRegistry()
        self.tool_executor: ToolExecutor | None = None
        self.skills: SkillCatalog | None = None
        self.mcp: McpManager | None = None
        self.registry: ToolRegistry | None = None
        self.dispatcher: ToolDispatcher | None = None
        self.retained_context: tuple[RetainedContextSection, ...] = ()
        self.selected_skill_context: tuple[RetainedContextSection, ...] = ()
        self.skill_catalog_snapshot: SkillCatalogSnapshot | None = None
        self.skill_access: SkillAccess | None = None
        self._skill_access_context_token = None
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
                async_kernel=self.async_kernel,
                sandbox=self.mcp_sandbox,
                resource_registry=self.resources,
            )
            external_entries = self.mcp.start()
            self.skill_catalog_snapshot = self.skills.catalog_snapshot(
                self.extension_snapshot
            )
            self.skill_access = SkillAccess.from_snapshot(
                self.skill_catalog_snapshot
            )
            self._skill_access_context_token = set_current_skill_access(
                self.skill_access
            )
            self._set_registry(external_entries)
            self._activate_mentions(self.user_input)
            self.retained_context = (
                self.skills.render_catalog(self.skill_catalog_snapshot),
            )
            self._select_skills(self.user_input, turn_id=self.run_id)
            return self
        except SkillReadError as error:
            self.close()
            raise RunResourceError(
                "SKILL_REFERENCE_AMBIGUOUS"
                if str(error) == "skill_reference_ambiguous"
                else "SKILL_SNAPSHOT_INVALID"
            ) from None
        except SkillAccessError as error:
            self.close()
            raise RunResourceError("SKILL_SNAPSHOT_INVALID") from error
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
                self.user_input = added
                self._activate_mentions(added)
                self._select_skills(
                    added,
                    turn_id=(
                        f"{self.run_id}:"
                        f"{hashlib.sha256(added.encode('utf-8')).hexdigest()}"
                    ),
                )
        except SkillReadError as error:
            raise RunResourceError(
                "SKILL_REFERENCE_AMBIGUOUS"
                if str(error) == "skill_reference_ambiguous"
                else "SKILL_SNAPSHOT_INVALID"
            ) from None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._skill_access_context_token is not None:
            reset_current_skill_access(self._skill_access_context_token)
            self._skill_access_context_token = None
        if self.mcp is not None:
            self.mcp.close()
        if self.tool_executor is not None:
            self.tool_executor.close()

    def __exit__(self, *_error: object) -> None:
        self.close()

    def _set_registry(
        self, external_entries: tuple[ToolRegistryEntry, ...]
    ) -> None:
        if (
            self.tool_executor is None
            or self.skills is None
            or self.skill_catalog_snapshot is None
        ):
            raise RuntimeError("run resources are not started")
        base = ToolRegistry.build(
            builtin_entries=(
                *self.tool_executor.registry.entries,
                *self.skills.tool_entries(self.skill_catalog_snapshot),
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

    def _select_skills(self, user_input: str, *, turn_id: str) -> None:
        if self.skills is None or self.skill_catalog_snapshot is None:
            raise RuntimeError("run resources are not started")
        selected = self.skills.select_explicit(
            self.skill_catalog_snapshot, turn_id, user_input
        )
        self.selected_skill_context = (
            self.skills.render_selected(self.skill_catalog_snapshot, selected)
            if selected.selected_qualified_ids
            else ()
        )
        if self.skill_access is not None:
            for qualified_id in selected.selected_qualified_ids:
                try:
                    self.skill_access.activate_explicit(qualified_id)
                except SkillAccessError:
                    # Existing snapshots can expose logical locators. They
                    # still support read-only context, but cannot grant a
                    # filesystem root until a trusted locator is available.
                    continue

    def activate_skill_model_read(self, qualified_id: str):
        if self.skill_access is None:
            raise RuntimeError("run resources are not started")
        return self.skill_access.activate_model_read(qualified_id)
