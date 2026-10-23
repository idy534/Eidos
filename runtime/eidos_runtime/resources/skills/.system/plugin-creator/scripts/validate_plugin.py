#!/usr/bin/env python3
"""Portable package preflight. Runtime import owns full manifest/Skill validation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys


MAX_PLUGIN_FILES = 512
MAX_PLUGIN_FILE_BYTES = 1024 * 1024
MAX_PLUGIN_TOTAL_BYTES = 8 * 1024 * 1024


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("duplicate JSON key")
        result[name] = value
    return result


def validate(root: Path) -> str | None:
    if root.is_symlink() or not root.is_dir() or root.stat().st_uid != os.getuid():
        return "plugin root must be a user-owned real directory"
    count, total = 0, 0
    for directory, names, filenames in os.walk(root, followlinks=False):
        for name in [*names, *filenames]:
            path = Path(directory) / name
            entry = path.lstat()
            if stat.S_ISLNK(entry.st_mode) or entry.st_uid != os.getuid():
                return "plugin entries must be user-owned without symbolic links"
            if name in names:
                if not stat.S_ISDIR(entry.st_mode):
                    return "plugin contains a special directory entry"
                continue
            if not stat.S_ISREG(entry.st_mode) or entry.st_nlink != 1:
                return "plugin contains an unsupported file"
            if name == ".DS_Store":
                continue
            count += 1
            total += entry.st_size
            if count > MAX_PLUGIN_FILES or entry.st_size > MAX_PLUGIN_FILE_BYTES or total > MAX_PLUGIN_TOTAL_BYTES:
                return "plugin is too large"
            if name == "SKILL.md":
                try:
                    lines = path.read_text(encoding="utf-8").splitlines()
                except UnicodeDecodeError:
                    return "SKILL.md must be UTF-8"
                if not lines or lines[0] != "---" or "---" not in lines[1:]:
                    return "SKILL.md needs YAML frontmatter"
    manifest = root / "plugin.json"
    if not manifest.is_file():
        manifest = root / ".codex-plugin/plugin.json"
    try:
        value = json.loads(manifest.read_text(encoding="utf-8"), object_pairs_hook=_unique)
        if not isinstance(value, dict):
            return "plugin manifest must be a JSON object"
    except (OSError, UnicodeDecodeError, ValueError):
        return "plugin manifest is missing or invalid"
    return None


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: validate_plugin.py <plugin-directory>", file=sys.stderr)
        raise SystemExit(1)
    error = validate(Path(sys.argv[1]))
    print(error or "Plugin preflight passed. Eidos validates declarations and Skill metadata during import.")
    raise SystemExit(1 if error else 0)
