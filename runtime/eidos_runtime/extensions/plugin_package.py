"""Read Eidos, portable Agent Plugins and Codex packages into one declaration."""
from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import PurePosixPath

from pydantic import JsonValue

from eidos_runtime.domain.hooks import HookDefinition, PluginCompatibility
from eidos_runtime.extensions.codex_hooks import parse_hooks
from eidos_runtime.extensions.contracts import PluginManifestV1
from eidos_runtime.models import EidosFrozenModel


class PluginPackage(EidosFrozenModel):
    manifest: PluginManifestV1
    hooks: tuple[HookDefinition, ...] = ()
    compatibility: PluginCompatibility = PluginCompatibility()


def _json(files: Mapping[str, bytes], path: str) -> dict[str, JsonValue]:
    value = json.loads(files[path].decode("utf-8"), object_pairs_hook=unique_object)
    if not isinstance(value, dict):
        raise ValueError("plugin_manifest_invalid")
    return value


def unique_object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    value: dict[str, JsonValue] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("plugin_duplicate_key")
        value[key] = item
    return value


def _path(value: str) -> str:
    if not value.startswith("./") or "\\" in value or "\x00" in value:
        raise ValueError("plugin_path_invalid")
    relative = PurePosixPath(value[2:])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("plugin_path_invalid")
    return relative.as_posix()


def read_package(files: Mapping[str, bytes]) -> PluginPackage:
    root = _json(files, "plugin.json") if "plugin.json" in files else None
    issues: list[str] = []
    if root is not None and "schemaVersion" in root:
        native = dict(root)
        hook_value = native.pop("hooks", None)
        manifest = PluginManifestV1.model_validate(native)
        settings = root
        source_format = "eidos"
        version = manifest.version
    else:
        overlay = _json(files, ".codex-plugin/plugin.json") if ".codex-plugin/plugin.json" in files else {}
        if root is None and not overlay:
            raise ValueError("plugin_manifest_missing")
        identity = root if root is not None else overlay
        extensions = identity.get("extensions", {})
        if not isinstance(extensions, dict):
            raise ValueError("plugin_extensions_invalid")
        inline = extensions.get("com.openai")
        if inline is not None and not isinstance(inline, dict):
            raise ValueError("plugin_extensions_invalid")
        settings = inline if isinstance(inline, dict) else overlay
        source_format = "portable" if root is not None else "codex"
        version = identity.get("version")
        if version is not None and not isinstance(version, str):
            raise ValueError("plugin_version_invalid")
        skill_locations = ["./skills/"] if root is not None else settings.get("skills", "./skills/")
        if isinstance(skill_locations, str):
            skill_locations = [skill_locations]
        if not isinstance(skill_locations, list) or not all(isinstance(path, str) for path in skill_locations):
            raise ValueError("plugin_skills_invalid")
        skills: list[dict[str, str]] = []
        for location in skill_locations:
            prefix = _path(location).rstrip("/")
            skills.extend({"root": path.removesuffix("/SKILL.md")} for path in sorted(files)
                          if path.startswith(prefix + "/") and path.endswith("/SKILL.md"))
        manifest = PluginManifestV1.model_validate({
            "schemaVersion": 1, "id": identity.get("name"), "name": identity.get("name"),
            "version": version or "0.0.0", "description": identity.get("description", ""),
            "skills": skills, "mcpServers": [],
        })
        hook_value = settings.get("hooks")
        if any(path in files for path in ("mcp.json", ".mcp.json")) or settings.get("mcpServers"):
            issues.append("bundled_mcp_requires_separate_configuration")
        if settings.get("apps") or ".app.json" in files:
            issues.append("codex_apps_not_supported")
    hooks: list[HookDefinition] = []
    if "hooks" not in settings:
        if "hooks/hooks.json" in files:
            hooks.extend(parse_hooks(_json(files, "hooks/hooks.json"), "hooks/hooks.json"))
    else:
        entries = hook_value if isinstance(hook_value, list) else [hook_value]
        for index, entry in enumerate(entries):
            if isinstance(entry, str):
                path = _path(entry)
                hooks.extend(parse_hooks(_json(files, path), path))
            elif isinstance(entry, dict):
                hooks.extend(parse_hooks(entry, f"manifest:{index}"))
            else:
                raise ValueError("plugin_hooks_invalid")
    if len(hooks) > 128 or len(json.dumps([hook.to_wire_dict() for hook in hooks]).encode("utf-8")) > 65536:
        raise ValueError("hook_capacity_exceeded")
    if len({hook.key for hook in hooks}) != len(hooks):
        raise ValueError("hook_source_repeated")
    issues.extend(f"hook_not_supported:{hook.key}" for hook in hooks if not hook.supported)
    return PluginPackage(manifest=manifest, hooks=tuple(hooks), compatibility=PluginCompatibility(
        format=source_format, declared_version=version, issues=tuple(issues),
    ))
