from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Callable

import yaml


logger = logging.getLogger(__name__)

MAX_SKILL_NAME_CHARS = 64
MAX_METADATA_BYTES = 64 * 1024
MAX_METADATA_STRING_CHARS = 1024
_SKILL_NAME = re.compile(r"^[^/\\\x00\r\n]{1,64}$")


class SkillManifestError(ValueError):
    """Raised when required SKILL.md metadata is malformed or incomplete."""


@dataclass(frozen=True)
class SkillManifest:
    name: str
    description: str
    short_description: str | None = None


@dataclass(frozen=True)
class SkillInterfaceMetadata:
    display_name: str | None = None
    short_description: str | None = None
    icon_small: Path | None = None
    icon_large: Path | None = None
    brand_color: str | None = None
    default_prompt: str | None = None


@dataclass(frozen=True)
class SkillToolDependency:
    type: str
    value: str
    description: str | None = None
    transport: str | None = None
    command: str | None = None
    url: str | None = None
    oauth_callback_port: int | None = None


@dataclass(frozen=True)
class SkillDependencies:
    tools: tuple[SkillToolDependency, ...] = ()


@dataclass(frozen=True)
class SkillPolicy:
    allow_implicit_invocation: bool | None = None
    products: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillAgentMetadata:
    interface: SkillInterfaceMetadata | None = None
    dependencies: SkillDependencies | None = None
    policy: SkillPolicy | None = None


def parse_skill_manifest(
    contents: str,
    default_name: str | Callable[[], str],
) -> SkillManifest:
    """Parse the shared, safe SKILL.md frontmatter contract."""

    frontmatter = _extract_frontmatter(contents)
    parsed = _load_yaml_with_repair(frontmatter)
    if not isinstance(parsed, dict):
        raise SkillManifestError("invalid YAML: frontmatter must be a mapping")

    raw_name = parsed.get("name")
    if raw_name is None or (isinstance(raw_name, str) and not raw_name.strip()):
        raw_name = default_name() if callable(default_name) else default_name
    elif not isinstance(raw_name, str):
        raise SkillManifestError("invalid name: must be a non-empty string")
    name = _sanitize_scalar(raw_name)
    if (
        not _SKILL_NAME.fullmatch(name)
        or name in {".", ".."}
        or len(name) > MAX_SKILL_NAME_CHARS
    ):
        raise SkillManifestError("invalid name: must be at most 64 characters")

    raw_description = parsed.get("description")
    if not isinstance(raw_description, str):
        raise SkillManifestError("missing field `description`")
    description = _sanitize_scalar(raw_description)
    if not description:
        raise SkillManifestError("missing field `description`")

    short_description: str | None = None
    raw_metadata = parsed.get("metadata")
    if raw_metadata is not None and not isinstance(raw_metadata, dict):
        raise SkillManifestError("invalid metadata: expected a mapping")
    if isinstance(raw_metadata, dict):
        candidate = raw_metadata.get("short-description")
        if candidate is None:
            candidate = raw_metadata.get("short_description")
        if candidate is not None and not isinstance(candidate, str):
            raise SkillManifestError(
                "invalid metadata.short-description: expected a string"
            )
        short_description = _optional_scalar(candidate, limit=None)

    return SkillManifest(name, description, short_description)


def load_skill_agent_metadata(skill_root: Path) -> SkillAgentMetadata:
    """Load optional agents/eidos.yaml metadata without blocking SKILL.md."""

    metadata_path = skill_root / "agents" / "eidos.yaml"
    try:
        metadata_bytes = _read_metadata_bytes(metadata_path)
        if metadata_bytes is None:
            return SkillAgentMetadata()
        value = _load_yaml(metadata_bytes.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("metadata root must be a mapping")
        return _parse_agent_metadata(value, skill_root)
    except (
        OSError,
        UnicodeDecodeError,
        TypeError,
        ValueError,
        yaml.YAMLError,
    ) as error:
        logger.warning("ignoring %s: invalid optional skill metadata: %s", metadata_path, error)
        return SkillAgentMetadata()


def _extract_frontmatter(contents: str) -> str:
    lines = contents.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillManifestError("missing YAML frontmatter delimited by ---")
    frontmatter: list[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            if not frontmatter:
                raise SkillManifestError("invalid YAML: empty frontmatter")
            return "\n".join(frontmatter)
        frontmatter.append(line)
    raise SkillManifestError("missing YAML frontmatter closing delimiter")


def _load_yaml_with_repair(frontmatter: str) -> object:
    try:
        return _load_yaml(frontmatter)
    except (TypeError, ValueError, yaml.YAMLError) as original_error:
        repaired = _repair_frontmatter_scalar_fields(frontmatter)
        if repaired is None:
            raise SkillManifestError(f"invalid YAML: {original_error}") from None
        try:
            return _load_yaml(repaired)
        except (TypeError, ValueError, yaml.YAMLError):
            raise SkillManifestError(f"invalid YAML: {original_error}") from None


def _load_yaml(contents: str) -> object:
    # Codex and the common skills ecosystem use PyYAML's safe loader. It
    # ignores unknown fields at the contract layer and keeps the parser's
    # normal last-value behavior for duplicate mappings.
    return yaml.safe_load(contents)


def _repair_frontmatter_scalar_fields(frontmatter: str) -> str | None:
    changed = False
    block_scalar_indent: int | None = None
    repaired_lines: list[str] = []
    for line in frontmatter.splitlines():
        indent = len(line) - len(line.lstrip(" "))
        if block_scalar_indent is not None:
            if not line.strip() or indent > block_scalar_indent:
                repaired_lines.append(line)
                continue
            block_scalar_indent = None

        separator = line.find(":")
        if separator < 0:
            repaired_lines.append(line)
            continue
        key = line[:separator]
        value = line[separator + 1 :]
        if not key.strip() or not value[:1].isspace():
            repaired_lines.append(line)
            continue

        trimmed = value.lstrip()
        leading = value[: len(value) - len(trimmed)]
        scalar, comment = _split_yaml_comment(trimmed)
        scalar = scalar.rstrip()
        if not scalar:
            repaired_lines.append(line)
            continue
        first = scalar[0]
        if first in "|>":
            block_scalar_indent = indent
            repaired_lines.append(line)
            continue
        if first in "'\"":
            repaired_lines.append(line)
            continue

        has_colon_separator = any(
            character == ":" and index + 1 < len(scalar) and scalar[index + 1].isspace()
            for index, character in enumerate(scalar)
        )
        invalid_flow_like_scalar = first in "[{@`" and _yaml_value_invalid(scalar)
        if invalid_flow_like_scalar and key.strip() in {"name", "description"}:
            repaired_lines.append(line)
            continue
        if not has_colon_separator and not invalid_flow_like_scalar:
            repaired_lines.append(line)
            continue

        quoted = scalar.replace("'", "''")
        repaired_lines.append(f"{key}:{leading}'{quoted}'{comment}")
        changed = True
    return "\n".join(repaired_lines) if changed else None


def _yaml_value_invalid(value: str) -> bool:
    try:
        _load_yaml(value)
    except (TypeError, ValueError, yaml.YAMLError):
        return True
    return False


def _split_yaml_comment(value: str) -> tuple[str, str]:
    for index, character in enumerate(value):
        if character == "#" and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip(), value[index:]
    return value, ""


def _sanitize_scalar(value: str) -> str:
    return " ".join(value.split())


def _optional_scalar(
    value: str | None,
    *,
    limit: int | None = MAX_METADATA_STRING_CHARS,
) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = _sanitize_scalar(value)
    if not normalized or (limit is not None and len(normalized) > limit):
        return None
    return normalized


def _read_metadata_bytes(path: Path) -> bytes | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or path.is_symlink()
        or metadata.st_size > MAX_METADATA_BYTES
    ):
        raise ValueError("metadata file is not a bounded regular file")
    return path.read_bytes()


def _parse_agent_metadata(value: dict[object, object], skill_root: Path) -> SkillAgentMetadata:
    interface = _parse_interface(value.get("interface"), skill_root)
    dependencies = _parse_dependencies(value.get("dependencies"))
    policy = _parse_policy(value.get("policy"))
    return SkillAgentMetadata(interface=interface, dependencies=dependencies, policy=policy)


def _parse_interface(value: object, skill_root: Path) -> SkillInterfaceMetadata | None:
    if not isinstance(value, dict):
        return None
    result = SkillInterfaceMetadata(
        display_name=_optional_scalar(_first(value, "display_name", "display-name", "displayName")),
        short_description=_optional_scalar(
            _first(value, "short_description", "short-description", "shortDescription")
        ),
        icon_small=_resolve_asset_path(
            _first(value, "icon_small", "icon-small", "iconSmall"), skill_root
        ),
        icon_large=_resolve_asset_path(
            _first(value, "icon_large", "icon-large", "iconLarge"), skill_root
        ),
        brand_color=_resolve_brand_color(_first(value, "brand_color", "brand-color", "brandColor")),
        default_prompt=_optional_scalar(
            _first(value, "default_prompt", "default-prompt", "defaultPrompt")
        ),
    )
    return result if any(value is not None for value in result.__dict__.values()) else None


def _parse_dependencies(value: object) -> SkillDependencies | None:
    if not isinstance(value, dict) or not isinstance(value.get("tools"), list):
        return None
    tools: list[SkillToolDependency] = []
    for item in value["tools"]:
        if not isinstance(item, dict):
            continue
        kind = _optional_scalar(_first(item, "type", "kind"))
        dependency_value = _optional_scalar(item.get("value"))
        if not kind or not dependency_value:
            continue
        callback = None
        oauth = item.get("oauth")
        if isinstance(oauth, dict):
            raw_callback = _first(oauth, "callback_port", "callback-port", "callbackPort")
            if isinstance(raw_callback, int) and not isinstance(raw_callback, bool) and 0 <= raw_callback <= 65535:
                callback = raw_callback
        tools.append(SkillToolDependency(
            type=kind,
            value=dependency_value,
            description=_optional_scalar(item.get("description")),
            transport=_optional_scalar(item.get("transport")),
            command=_optional_scalar(item.get("command")),
            url=_optional_scalar(item.get("url")),
            oauth_callback_port=callback,
        ))
    return SkillDependencies(tuple(tools)) if tools else None


def _parse_policy(value: object) -> SkillPolicy | None:
    if not isinstance(value, dict):
        return None
    allow = value.get("allow_implicit_invocation")
    if not isinstance(allow, bool):
        allow = None
    products_value = value.get("products")
    products: tuple[str, ...] = ()
    if isinstance(products_value, list):
        products = tuple(
            normalized
            for item in products_value
            if (normalized := _optional_scalar(item)) is not None
        )
    return SkillPolicy(allow_implicit_invocation=allow, products=products)


def _first(value: dict[object, object], *keys: str) -> object:
    for key in keys:
        if key in value:
            return value[key]
    return None


def _resolve_brand_color(value: object) -> str | None:
    normalized = _optional_scalar(value)
    if normalized is None or not re.fullmatch(r"#[0-9A-Fa-f]{6}", normalized):
        return None
    return normalized


def _resolve_asset_path(value: object, skill_root: Path) -> Path | None:
    if not isinstance(value, str) or not value or len(value) > 512:
        return None
    if "\\" in value:
        return None
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or not relative.parts
        or relative.parts[0] != "assets"
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        logger.warning("ignoring asset path %s: path must stay under assets", value)
        return None
    assets_root = skill_root / "assets"
    candidate = skill_root.joinpath(*relative.parts)
    try:
        assets_metadata = assets_root.lstat()
        if not stat.S_ISDIR(assets_metadata.st_mode) or stat.S_ISLNK(assets_metadata.st_mode):
            return None
        canonical_root = assets_root.resolve(strict=True)
        canonical_candidate = candidate.resolve(strict=True)
    except OSError:
        return None
    if not canonical_candidate.is_relative_to(canonical_root) or not canonical_candidate.is_file():
        logger.warning("ignoring asset path %s: resolved path escapes assets", value)
        return None
    return canonical_candidate


__all__ = [
    "MAX_METADATA_BYTES",
    "SkillAgentMetadata",
    "SkillDependencies",
    "SkillInterfaceMetadata",
    "SkillManifest",
    "SkillManifestError",
    "SkillPolicy",
    "SkillToolDependency",
    "load_skill_agent_metadata",
    "parse_skill_manifest",
]
