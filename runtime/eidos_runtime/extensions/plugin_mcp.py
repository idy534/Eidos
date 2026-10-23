"""Normalize packaged stdio declarations without executing or installing them."""
from __future__ import annotations

from collections.abc import Mapping
import re

from pydantic import JsonValue

from eidos_runtime.extensions.contracts import McpServerConfigV1


def read_mcp_servers(files: Mapping[str, bytes], settings: dict[str, JsonValue], *,
                     portable: bool) -> tuple[list[McpServerConfigV1], list[str]]:
    declaration = settings.get("mcpServers")
    default = "mcp.json" if portable else ".mcp.json"
    if declaration is None:
        declaration = "./" + default if default in files else {}
    if isinstance(declaration, str):
        # Import lazily to share the exact package path and duplicate-key rules.
        from eidos_runtime.extensions.plugin_package import _json, _path
        declaration = _json(files, _path(declaration)).get("mcpServers", {})
    if not isinstance(declaration, dict) or len(declaration) > 32:
        raise ValueError("plugin_mcp_invalid")
    servers: list[McpServerConfigV1] = []
    issues: list[str] = []
    for name, value in declaration.items():
        McpServerConfigV1.validate_id(name)
        if not isinstance(value, dict):
            raise ValueError("plugin_mcp_invalid")
        transport = value.get("type", "stdio")
        if transport in {"streamable-http", "http"}:
            if set(value) - {"type", "url", "extensions", "disabled"}:
                issues.append(f"mcp_settings_unsupported:{name}")
                continue
            extensions = value.get("extensions", {})
            eidos = extensions.get("com.eidos", {}) if isinstance(extensions, dict) else None
            if not isinstance(eidos, dict) or set(eidos) - {"auth", "startupTimeoutSeconds", "toolTimeoutSeconds"}:
                raise ValueError("plugin_mcp_invalid")
            # OpenAI-only auth declarations need an explicit Eidos equivalent.
            if "com.openai" in extensions and not eidos.get("auth"):
                issues.append(f"mcp_settings_unsupported:{name}")
                continue
            disabled = value.get("disabled", False)
            if not isinstance(disabled, bool):
                raise ValueError("plugin_mcp_invalid")
            servers.append(McpServerConfigV1.model_validate({
                "id": name, "transport": "streamable-http", "url": value.get("url"),
                "auth": eidos.get("auth"), "permissionProfile": "connector",
                "enabled": not disabled,
                "startupTimeoutSeconds": eidos.get("startupTimeoutSeconds", 15),
                "toolTimeoutSeconds": eidos.get("toolTimeoutSeconds", 60),
            }))
            continue
        if transport != "stdio" or "url" in value:
            issues.append(f"mcp_transport_unsupported:{name}")
            continue
        if set(value) - {"type", "command", "args", "env", "envNames", "disabled", "extensions"}:
            issues.append(f"mcp_settings_unsupported:{name}")
            continue
        environment = value.get("env", {})
        if not isinstance(environment, dict):
            raise ValueError("plugin_mcp_invalid")
        bindings: dict[str, str] = {}
        for key, binding in environment.items():
            match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", binding) if isinstance(binding, str) else None
            if match is None:
                break
            bindings[key] = match.group(1)
        else:
            extension = value.get("extensions", {})
            if not isinstance(extension, dict):
                raise ValueError("plugin_mcp_invalid")
            eidos = extension.get("com.eidos", {})
            if not isinstance(eidos, dict) or set(eidos) - {"permissionProfile", "startupTimeoutSeconds", "toolTimeoutSeconds"}:
                raise ValueError("plugin_mcp_invalid")
            disabled = value.get("disabled", False)
            if not isinstance(disabled, bool):
                raise ValueError("plugin_mcp_invalid")
            config = McpServerConfigV1.model_validate({
                "id": name, "executable": value.get("command"), "argv": value.get("args", []),
                "envNames": value.get("envNames", list(bindings)), "envBindings": bindings,
                "permissionProfile": eidos.get("permissionProfile", "workspace_read"),
                "startupTimeoutSeconds": eidos.get("startupTimeoutSeconds", 15),
                "toolTimeoutSeconds": eidos.get("toolTimeoutSeconds", 60), "enabled": not disabled,
            })
            supported_variables = {"PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT", "RUNTIME_PYTHON"}
            if any(variable not in supported_variables for argument in [config.executable, *config.argv]
                   for variable in re.findall(r"\$\{([^}]+)\}", argument)):
                issues.append(f"mcp_settings_unsupported:{name}")
            else:
                servers.append(config)
            continue
        # Credential values are supplied through the host environment, never SQLite.
        issues.append(f"mcp_environment_unsupported:{name}")
    return servers, issues


def resolve_package_mcp(config: McpServerConfigV1, root: str, python: str) -> McpServerConfigV1:
    def expand(value: str) -> str:
        for name, replacement in (("PLUGIN_ROOT", root), ("CLAUDE_PLUGIN_ROOT", root), ("RUNTIME_PYTHON", python)):
            value = value.replace("${" + name + "}", replacement)
        return value
    return config.model_copy(update={"executable": expand(config.executable),
                                     "argv": [expand(value) for value in config.argv]})
