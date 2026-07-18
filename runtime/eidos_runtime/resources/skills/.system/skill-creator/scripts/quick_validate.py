#!/usr/bin/env python3
"""Validate the closed Eidos SKILL.md contract."""

from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import sys


SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ALLOWED_DIRECTORIES = {"scripts", "references"}
MAX_FILE_BYTES = 256 * 1024
MAX_FILES = 512
MAX_TOTAL_BYTES = 8 * 1024 * 1024


def validate_skill(skill_path: str) -> tuple[bool, str]:
    root = Path(skill_path)
    skill_file = root / "SKILL.md"
    if root.is_symlink() or skill_file.is_symlink() or not skill_file.is_file():
        return False, "SKILL.md is missing or unsafe"
    try:
        root_metadata = root.lstat()
    except OSError:
        return False, "skill directory is unreadable"
    if (
        root_metadata.st_uid != os.getuid()
        or stat.S_IMODE(root_metadata.st_mode) != 0o700
    ):
        return False, "skill directory must be owned by the user with mode 0700"
    file_count = 0
    total_bytes = 0
    for directory, names, filenames in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in [*names, *filenames]:
            candidate = parent / name
            try:
                metadata = candidate.lstat()
            except OSError:
                return False, "skill resource is unreadable"
            if stat.S_ISLNK(metadata.st_mode) or metadata.st_uid != os.getuid():
                return False, "skill resources must be owned regular files without symlinks"
            if name in names:
                if (
                    not stat.S_ISDIR(metadata.st_mode)
                    or stat.S_IMODE(metadata.st_mode) != 0o700
                    or (parent == root and name not in ALLOWED_DIRECTORIES)
                ):
                    return False, "skill resource directories are invalid"
                continue
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_size > MAX_FILE_BYTES
            ):
                return False, "skill resources must be private files of at most 256 KiB"
            try:
                data = candidate.read_bytes()
                data.decode("utf-8")
            except (OSError, UnicodeDecodeError):
                return False, "skill resources must be UTF-8 text"
            if b"\x00" in data:
                return False, "skill resources must be UTF-8 text"
            file_count += 1
            total_bytes += len(data)
            if file_count > MAX_FILES or total_bytes > MAX_TOTAL_BYTES:
                return False, "skill contains too many resources"
    try:
        content = skill_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False, "SKILL.md must be readable UTF-8"
    lines = content.splitlines()
    if not lines or lines[0] != "---":
        return False, "YAML frontmatter is missing"
    values: dict[str, str] = {}
    for line in lines[1:]:
        if line == "---":
            break
        if ":" not in line:
            return False, "frontmatter is invalid"
        key, value = line.split(":", 1)
        key, value = key.strip(), value.strip().strip("\"'")
        if key not in {"name", "description"} or key in values:
            return False, "frontmatter accepts exactly name and description"
        values[key] = value
    else:
        return False, "frontmatter is not closed"
    name, description = values.get("name", ""), values.get("description", "")
    if set(values) != {"name", "description"}:
        return False, "frontmatter requires name and description"
    if not SKILL_NAME.fullmatch(name) or len(name) > 64 or root.name != name:
        return False, "folder and name must match lowercase hyphen-case"
    if not description or len(description.encode("utf-8")) > 1024:
        return False, "description must be 1-1024 UTF-8 bytes"
    if re.search(r"(?m)^\[?TODO\b", content):
        return False, "remove all TODO placeholders"
    return True, "Skill is valid"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: quick_validate.py <skill-directory>", file=sys.stderr)
        raise SystemExit(1)
    valid, message = validate_skill(sys.argv[1])
    print(message)
    raise SystemExit(0 if valid else 1)
