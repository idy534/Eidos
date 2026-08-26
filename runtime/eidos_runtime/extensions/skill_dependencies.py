from __future__ import annotations

import hashlib
import json
from typing import Literal

from eidos_runtime.extensions.skill_manifest import SkillToolDependency
from eidos_runtime.extensions.skills import (
    RetainedContextSection,
    SkillCatalog,
    SkillCatalogSnapshot,
)
from eidos_runtime.models import EidosFrozenStrictModel


class SkillMcpDependencyDiagnostic(EidosFrozenStrictModel):
    qualified_id: str
    value: str
    status: Literal["installed", "missing", "unsupported"]
    description: str | None = None
    transport: str | None = None
    reason: str | None = None


def diagnose_skill_mcp_dependencies(
    catalog: SkillCatalog,
    snapshot: SkillCatalogSnapshot,
    qualified_ids: tuple[str, ...],
    servers: list[dict[str, object]],
    extension_snapshot: dict[str, object],
) -> tuple[SkillMcpDependencyDiagnostic, ...]:
    available_servers = tuple(
        server
        for server in servers
        if _server_is_in_snapshot(server, extension_snapshot)
    )
    diagnostics: list[SkillMcpDependencyDiagnostic] = []
    for qualified_id in sorted(set(qualified_ids), key=str.encode):
        metadata = catalog.metadata(snapshot, qualified_id)
        dependencies = metadata.dependencies
        if dependencies is None:
            continue
        for dependency in dependencies.tools:
            if dependency.type.casefold() != "mcp":
                continue
            diagnostics.append(
                _diagnose_dependency(
                    qualified_id, dependency, available_servers
                )
            )
    return tuple(sorted(
        diagnostics,
        key=lambda value: (
            value.qualified_id.encode("utf-8"),
            value.value.encode("utf-8"),
        ),
    ))


def render_skill_dependency_warning(
    diagnostics: tuple[SkillMcpDependencyDiagnostic, ...],
) -> RetainedContextSection | None:
    unresolved = tuple(
        value for value in diagnostics if value.status != "installed"
    )
    if not unresolved:
        return None
    payload = [
        value.model_dump(mode="json")
        for value in unresolved
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return RetainedContextSection(
        section_id="skill-dependency-warning",
        version=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        role="user",
        source="skill-dependencies",
        content=(
            "Selected Skill MCP dependency status: "
            f"{encoded}. Eidos did not install or enable any MCP server. "
            "Continue with available tools or tell the user which dependency is missing."
        ),
    )


def _diagnose_dependency(
    qualified_id: str,
    dependency: SkillToolDependency,
    servers: tuple[dict[str, object], ...],
) -> SkillMcpDependencyDiagnostic:
    transport = (dependency.transport or "stdio").casefold()
    if transport not in {"stdio", "local"} or dependency.url is not None:
        return SkillMcpDependencyDiagnostic(
            qualified_id=qualified_id,
            value=dependency.value,
            status="unsupported",
            description=dependency.description,
            transport=dependency.transport,
            reason="eidos_mcp_transport_unsupported",
        )
    matched = tuple(
        server for server in servers if _server_matches(dependency, server)
    )
    if any(server.get("available") is True for server in matched):
        return SkillMcpDependencyDiagnostic(
            qualified_id=qualified_id,
            value=dependency.value,
            status="installed",
            description=dependency.description,
            transport=dependency.transport,
        )
    return SkillMcpDependencyDiagnostic(
        qualified_id=qualified_id,
        value=dependency.value,
        status="missing",
        description=dependency.description,
        transport=dependency.transport,
        reason=(
            "configured_but_unavailable"
            if matched
            else "mcp_server_not_configured"
        ),
    )


def _server_matches(
    dependency: SkillToolDependency,
    server: dict[str, object],
) -> bool:
    plugin_id = str(server.get("pluginId", ""))
    server_id = str(server.get("serverId", ""))
    identities = {server_id, f"{plugin_id}:{server_id}"}
    if dependency.value not in identities:
        return False
    if dependency.command is not None:
        return dependency.command == server.get("executable")
    return True


def _server_is_in_snapshot(
    server: dict[str, object], snapshot: dict[str, object]
) -> bool:
    plugins = snapshot.get("plugins")
    if not isinstance(plugins, list):
        return False
    return any(
        isinstance(plugin, dict)
        and plugin.get("id") == server.get("pluginId")
        and plugin.get("contentHash") == server.get("pluginHash")
        for plugin in plugins
    )


__all__ = [
    "SkillMcpDependencyDiagnostic",
    "diagnose_skill_mcp_dependencies",
    "render_skill_dependency_warning",
]
