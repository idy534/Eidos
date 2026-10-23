"""Bounded display metadata from a package; it never grants capabilities."""
from __future__ import annotations

import base64
from collections.abc import Mapping
from urllib.parse import urlsplit

from pydantic import JsonValue

from eidos_runtime.domain.plugins import PluginPresentation


def read_presentation(identity: dict[str, JsonValue], settings: dict[str, JsonValue],
                      files: Mapping[str, bytes], plugin_id: str,
                      skill_roots: tuple[str, ...]) -> PluginPresentation | None:
    from eidos_runtime.extensions.plugin_package import _path
    interface = settings.get("interface", {})
    if not isinstance(interface, dict):
        raise ValueError("plugin_interface_invalid")
    def text(value: JsonValue | None, limit: int) -> str | None:
        return value.strip()[:limit] or None if isinstance(value, str) else None
    author = identity.get("author", {})
    developer = text(interface.get("developerName"), 128) or text(
        author.get("name") if isinstance(author, dict) else author, 128,
    )
    website = text(interface.get("websiteURL"), 2048) or text(identity.get("homepage"), 2048)
    if website:
        parsed = urlsplit(website)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.username or parsed.password:
            website = None
    icon = None
    for value in (interface.get("composerIcon"), interface.get("logo")):
        if not isinstance(value, str):
            continue
        path = _path(value)
        data = files.get(path, b"")
        if len(data) > 65536:
            continue
        mime = "image/png" if data.startswith(b"\x89PNG\r\n\x1a\n") else (
            "image/jpeg" if data.startswith(b"\xff\xd8\xff") else None)
        if mime:
            icon = f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
            break
    onboarding = settings.get("onboardingSkill")
    # Older Codex overlays nest this field under extensions.com.openai.
    if onboarding is None:
        extensions = settings.get("extensions")
        if isinstance(extensions, dict) and isinstance(extensions.get("com.openai"), dict):
            onboarding = extensions["com.openai"].get("onboardingSkill")
    onboarding_id = None
    if onboarding is not None:
        if not isinstance(onboarding, str):
            raise ValueError("plugin_onboarding_invalid")
        path = _path(onboarding)
        if not path.endswith("/SKILL.md") or path.removesuffix("/SKILL.md") not in skill_roots:
            raise ValueError("plugin_onboarding_invalid")
        from eidos_runtime.extensions.skill_manifest import parse_skill_manifest
        name = parse_skill_manifest(files[path].decode("utf-8"), path.split("/")[-2]).name
        onboarding_id = f"{plugin_id}:{name}"
    metadata = PluginPresentation(developer_name=developer, website_url=website,
                                  license=text(identity.get("license"), 128), icon=icon,
                                  onboarding_skill_id=onboarding_id)
    return metadata if any(value is not None for value in metadata.model_dump().values()) else None
