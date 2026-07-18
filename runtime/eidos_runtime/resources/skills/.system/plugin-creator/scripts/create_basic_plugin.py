#!/usr/bin/env python3
"""Create a minimal local Eidos Plugin v1."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys
import uuid


IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")


def _write(path: Path, value: str) -> None:
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    try:
        data = value.encode("utf-8")
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Create an Eidos Plugin v1.")
    parser.add_argument("plugin_id")
    parser.add_argument("--path", default="~/eidos-plugins", help="Parent directory")
    parser.add_argument("--name")
    parser.add_argument("--description", default="Local Eidos plugin")
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--skill", action="append", default=[])
    args = parser.parse_args(argv)

    plugin_id = re.sub(r"-+", "-", re.sub(r"[^a-z0-9_-]+", "-", args.plugin_id.lower())).strip("-")
    skills = list(dict.fromkeys(args.skill))
    if not IDENTIFIER.fullmatch(plugin_id):
        print("Error: invalid plugin id.", file=sys.stderr)
        return 1
    if not VERSION.fullmatch(args.version):
        print("Error: version must be a valid semantic version.", file=sys.stderr)
        return 1
    plugin_name = args.name or " ".join(part.capitalize() for part in plugin_id.split("-"))
    if not 1 <= len(plugin_name) <= 128:
        print("Error: name must contain 1-128 characters.", file=sys.stderr)
        return 1
    if len(args.description) > 1024:
        print("Error: description must contain at most 1024 characters.", file=sys.stderr)
        return 1
    if any(not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) for name in skills):
        print("Error: skill names must be lowercase hyphen-case.", file=sys.stderr)
        return 1
    root = Path(args.path).expanduser()
    destination = root / plugin_id
    staging = root / f".plugin-{uuid.uuid4().hex}"
    try:
        if root.is_symlink():
            raise ValueError("parent path must not be a symbolic link")
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if destination.exists():
            raise ValueError("plugin already exists")
        staging.mkdir(mode=0o700)
        declarations = []
        for skill_name in skills:
            skill_root = staging / "skills" / skill_name
            skill_root.mkdir(mode=0o700, parents=True)
            _write(skill_root / "SKILL.md", (
                f"---\nname: {skill_name}\n"
                f"description: TODO describe when to use {skill_name}.\n---\n\n"
                f"# {' '.join(part.capitalize() for part in skill_name.split('-'))}\n\nTODO add instructions.\n"
            ))
            declarations.append({"root": f"skills/{skill_name}"})
        manifest = {
            "schemaVersion": 1,
            "id": plugin_id,
            "name": plugin_name,
            "version": args.version,
            "description": args.description,
            "skills": declarations,
            "mcpServers": [],
        }
        _write(staging / "plugin.json", json.dumps(
            manifest, ensure_ascii=False, indent=2, sort_keys=False
        ) + "\n")
        os.replace(staging, destination)
        print(f"Created {plugin_id} at {destination}")
        print("Remove TODOs, then run validate_plugin.py.")
        return 0
    except (OSError, ValueError) as error:
        shutil.rmtree(staging, ignore_errors=True)
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
