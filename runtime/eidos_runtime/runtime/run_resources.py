from __future__ import annotations

import hashlib
import re

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.extensions.mcp import McpManager
from eidos_runtime.extensions.plugins import PluginCatalog
from eidos_runtime.extensions.skill_dependencies import (
    SkillMcpDependencyDiagnostic,
    diagnose_skill_mcp_dependencies,
    render_skill_dependency_warning,
)
from eidos_runtime.extensions.skill_access import (
    SkillAccess,
    SkillAccessError,
)
from eidos_runtime.extensions.skills import (
    RetainedContextSection,
    SkillCatalog,
    SkillCatalogSnapshot,
    SkillReadError,
)
from eidos_runtime.runtime.tool_dispatcher import ToolDispatcher
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.runtime.resource_registry import ResourceRegistry
from eidos_runtime.runtime.runtime_dependencies import (
    RuntimeDependencyCatalog,
    RuntimeDependencyCatalogError,
    RuntimeDependencyCoordinator,
)
from eidos_runtime.tools.registry import ToolRegistry, ToolRegistryEntry
from eidos_runtime.tools.read_tool_output import read_tool_output_entry
from eidos_runtime.tools.search import tool_search_entry
from eidos_runtime.tools.runtime_workspace import ToolExecutor
from eidos_runtime.tools.view_image import ViewImageRootAuthority, view_image_entry
from eidos_runtime.tools.workspace_dependencies import workspace_dependencies_entry


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
        supports_images: bool = False,
        runtime_dependency_catalog: RuntimeDependencyCatalog | None = None,
        events: RuntimeEvents | None = None,
    ) -> None:
        self.store = store
        self.run_id = run_id
        self.extension_snapshot = extension_snapshot
        self.user_input = user_input
        self.mcp_sandbox = mcp_sandbox
        self.supports_images = supports_images
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
        self.runtime_dependencies: RuntimeDependencyCoordinator | None = None
        self.runtime_dependency_catalog = runtime_dependency_catalog
        self.events = events
        self.skill_dependency_diagnostics: tuple[
            SkillMcpDependencyDiagnostic, ...
        ] = ()
        self._external_entries: tuple[ToolRegistryEntry, ...] = ()
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
            self._external_entries = self.mcp.start()
            self.skill_catalog_snapshot = self.skills.catalog_snapshot(
                self.extension_snapshot
            )
            self.skill_access = SkillAccess.from_snapshot(
                self.skill_catalog_snapshot,
            )
            self.runtime_dependencies = RuntimeDependencyCoordinator.for_run(
                self.store,
                self.run_id,
                catalog=self.runtime_dependency_catalog,
                skills=self.skills,
                skill_snapshot=self.skill_catalog_snapshot,
                events=self.events,
            )
            self._set_registry()
            self._activate_mentions(self.user_input)
            self.retained_context = (
                self.skills.render_catalog(self.skill_catalog_snapshot),
            )
            self._select_skills(self.user_input, turn_id=self.run_id)
            self._refresh_skill_dependency_diagnostics()
            self._set_registry()
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
                self._external_entries = entries
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
            self._refresh_skill_dependency_diagnostics()
            self._set_registry()
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
        if self.mcp is not None:
            self.mcp.close()
        if self.tool_executor is not None:
            self.tool_executor.close()

    def __exit__(self, *_error: object) -> None:
        self.close()

    def _set_registry(
        self, external_entries: tuple[ToolRegistryEntry, ...] | None = None
    ) -> None:
        if (
            self.tool_executor is None
            or self.skills is None
            or self.skill_catalog_snapshot is None
        ):
            raise RuntimeError("run resources are not started")
        if external_entries is not None:
            self._external_entries = external_entries
        image_entry = (
            view_image_entry(
                supports_images=True,
                authority=self.image_authority,
            )
            if self.supports_images
            else None
        )
        base = ToolRegistry.build(
            builtin_entries=(
                *self.tool_executor.registry.entries,
                read_tool_output_entry(self.store, self.run_id),
                workspace_dependencies_entry(
                    metadata_provider=self._workspace_dependency_metadata,
                ),
                *self.skills.tool_entries(
                    self.skill_catalog_snapshot,
                    activate_model_read=self.activate_skill_model_read,
                ),
                *((image_entry,) if image_entry is not None else ()),
            ),
            external_entries=self._external_entries,
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
                self.skill_access.activate_explicit(qualified_id)
                self._bind_skill(qualified_id)

    def activate_skill_model_read(self, qualified_id: str):
        if self.skill_access is None:
            raise RuntimeError("run resources are not started")
        record = self.skill_access.activate_model_read(qualified_id)
        self._bind_skill(qualified_id)
        self._refresh_skill_dependency_diagnostics()
        return record

    def image_authority(self) -> ViewImageRootAuthority:
        if self.skill_access is None:
            raise RuntimeError("run resources are not started")
        workspace = self.store.workspace_for_run(self.run_id)
        return ViewImageRootAuthority.from_skill_access(
            workspace.path,
            self.skill_access,
        )

    def _refresh_skill_dependency_diagnostics(self) -> None:
        if (
            self.skills is None
            or self.skill_catalog_snapshot is None
            or self.skill_access is None
        ):
            return
        self.skill_dependency_diagnostics = diagnose_skill_mcp_dependencies(
            self.skills,
            self.skill_catalog_snapshot,
            tuple(record.qualified_id for record in self.skill_access.records()),
            self.skills.plugins.list_mcp_servers(),
            self.extension_snapshot,
        )
        warning = render_skill_dependency_warning(
            self.skill_dependency_diagnostics
        )
        runtime_warning = (
            self.runtime_dependencies.skill_dependency_warning(
                tuple(record.qualified_id for record in self.skill_access.records())
            )
            if self.runtime_dependencies is not None
            else None
        )
        catalog_context = tuple(
            section
            for section in self.retained_context
            if section.section_id == "skill-catalog"
        )
        self.retained_context = (
            *catalog_context,
            *((warning,) if warning is not None else ()),
            *((runtime_warning,) if runtime_warning is not None else ()),
        )

    def _bind_skill(self, qualified_id: str) -> None:
        if self.runtime_dependencies is None:
            return
        try:
            self.runtime_dependencies.binding_for_skill(qualified_id)
        except RuntimeDependencyCatalogError:
            # An invalid or missing Skill declaration is model-visible when
            # its script is attempted. It must not fail the whole Run.
            return

    def _workspace_dependency_metadata(self) -> dict[str, object]:
        if self.runtime_dependencies is None or self.skill_access is None:
            return {
                "defaultDependencyBindingId": None,
                "activeSkillDependencyBindings": [],
            }
        return self.runtime_dependencies.workspace_dependencies_metadata(
            tuple(record.qualified_id for record in self.skill_access.records())
        )
