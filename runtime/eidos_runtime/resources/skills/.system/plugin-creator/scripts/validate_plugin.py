#!/usr/bin/env python3
"""Validate the closed local Eidos Plugin v1 manifest."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys


IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
MANIFEST_KEYS = {"schemaVersion", "id", "name", "version", "description", "skills", "mcpServers"}
SERVER_KEYS = {
    "id", "executable", "argv", "envNames", "permissionProfile",
    "startupTimeoutSeconds", "toolTimeoutSeconds", "enabled",
}
MAX_PLUGIN_FILES = 512
MAX_PLUGIN_FILE_BYTES = 1024 * 1024
MAX_PLUGIN_TOTAL_BYTES = 8 * 1024 * 1024


def _matches(pattern: re.Pattern[str], value: object) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _valid_skill(skill_root: Path) -> bool:
    try:
        lines = (skill_root / "SKILL.md").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return False
    if not lines or lines[0] != "---":
        return False
    values: dict[str, str] = {}
    for line in lines[1:]:
        if line == "---":
            break
        if ":" not in line:
            return False
        key, value = line.split(":", 1)
        key, value = key.strip(), value.strip().strip("\"'")
        if key not in {"name", "description"} or key in values:
            return False
        values[key] = value
    else:
        return False
    return (
        set(values) == {"name", "description"}
        and _matches(re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$"), values["name"])
        and skill_root.name == values["name"]
        and bool(values["description"])
        and len(values["description"].encode("utf-8")) <= 1024
        and not any(re.match(r"^\[?TODO\b", line) for line in lines)
    )


def _relative(value: object) -> bool:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
        return False
    return all(part not in {"", ".", ".."} for part in PurePosixPath(value).parts)


def validate(root: Path) -> str | None:
    if root.is_symlink() or not root.is_dir():
        return "plugin root must be a real directory"
    file_count = 0
    total_bytes = 0
    for directory, names, filenames in os.walk(root, followlinks=False):
        for name in [*names, *filenames]:
            entry = (Path(directory) / name).lstat()
            if stat.S_ISLNK(entry.st_mode) or entry.st_uid != os.getuid():
                return "plugin entries must be owned by the user without symbolic links"
            if name in names and not stat.S_ISDIR(entry.st_mode):
                return "plugin contains a special directory entry"
            if name in filenames:
                if not stat.S_ISREG(entry.st_mode) or entry.st_size > MAX_PLUGIN_FILE_BYTES:
                    return "plugin contains an unsupported file"
                file_count += 1
                total_bytes += entry.st_size
                if file_count > MAX_PLUGIN_FILES or total_bytes > MAX_PLUGIN_TOTAL_BYTES:
                    return "plugin is too large"
    try:
        manifest = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return "plugin.json must be valid UTF-8 JSON"
    if not isinstance(manifest, dict) or set(manifest) != MANIFEST_KEYS:
        return "plugin.json has missing or unknown fields"
    if manifest["schemaVersion"] != 1 or not _matches(IDENTIFIER, manifest.get("id")):
        return "schemaVersion or id is invalid"
    if root.name != manifest["id"]:
        return "plugin folder and id must match"
    if not isinstance(manifest.get("name"), str) or not 1 <= len(manifest["name"]) <= 128:
        return "name is invalid"
    if not _matches(VERSION, manifest.get("version")):
        return "version is invalid"
    if not isinstance(manifest.get("description"), str) or len(manifest["description"]) > 1024:
        return "description is invalid"
    skills = manifest.get("skills")
    if not isinstance(skills, list) or len(skills) > 64:
        return "skills is invalid"
    roots: list[str] = []
    for skill in skills:
        if not isinstance(skill, dict) or set(skill) != {"root"} or not _relative(skill.get("root")):
            return "skill declaration is invalid"
        skill_root = str(skill["root"])
        if not _valid_skill(root.joinpath(*PurePosixPath(skill_root).parts)):
            return "declared skill metadata is invalid"
        roots.append(skill_root)
    if len(set(roots)) != len(roots):
        return "skill declarations are duplicated"
    servers = manifest.get("mcpServers")
    if not isinstance(servers, list) or len(servers) > 32:
        return "mcpServers is invalid"
    server_ids: list[str] = []
    for server in servers:
        if not isinstance(server, dict) or not set(server) <= SERVER_KEYS:
            return "MCP server has unknown fields"
        if not {"id", "executable", "permissionProfile"} <= set(server):
            return "MCP server has missing fields"
        if not _matches(IDENTIFIER, server.get("id")):
            return "MCP server id is invalid"
        executable = server.get("executable")
        if not isinstance(executable, str) or not executable or any(char in executable for char in "\x00\r\n"):
            return "MCP executable is invalid"
        executable_path = PurePosixPath(executable)
        if len(executable_path.parts) > 1 and not executable_path.is_absolute():
            if not root.joinpath(*executable_path.parts).is_file():
                return "relative MCP executable is missing"
        argv = server.get("argv", [])
        env_names = server.get("envNames", [])
        if not isinstance(argv, list) or len(argv) > 64 or not all(isinstance(value, str) for value in argv):
            return "MCP argv is invalid"
        if not isinstance(env_names, list) or len(env_names) > 64 or len(set(env_names)) != len(env_names) or not all(isinstance(value, str) and ENV_NAME.fullmatch(value) for value in env_names):
            return "MCP envNames is invalid"
        if server.get("permissionProfile") not in {"connector", "workspace_read"}:
            return "MCP permissionProfile is invalid"
        startup = server.get("startupTimeoutSeconds", 15)
        timeout = server.get("toolTimeoutSeconds", 60)
        if not isinstance(startup, int) or isinstance(startup, bool) or not 1 <= startup <= 60:
            return "MCP startup timeout is invalid"
        if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 600:
            return "MCP tool timeout is invalid"
        if not isinstance(server.get("enabled", False), bool):
            return "MCP enabled is invalid"
        server_ids.append(str(server["id"]))
    if len(set(server_ids)) != len(server_ids):
        return "MCP server ids are duplicated"
    return None


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: validate_plugin.py <plugin-directory>", file=sys.stderr)
        raise SystemExit(1)
    error = validate(Path(sys.argv[1]))
    print(error or "Plugin is valid")
    raise SystemExit(1 if error else 0)
