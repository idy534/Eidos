#!/usr/bin/env python3
"""Create a minimal Eidos user skill with private permissions."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import sys
import uuid


SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
RESOURCES = {"scripts", "references"}
TEMPLATE = """---
name: {name}
description: TODO describe what this skill does and when Eidos should use it.
---

# {title}

TODO add the smallest workflow and resource guidance needed for this skill.
"""


def _default_root() -> Path:
    return Path(os.environ.get("EIDOS_DATA_DIR", "~/.eidos")).expanduser() / "skills"


def _private_directory(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("destination must not be a symbolic link")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.stat().st_uid != os.getuid():
        raise ValueError("destination owner is invalid")
    os.chmod(path, 0o700)


def _write(path: Path, content: str, mode: int = 0o600) -> None:
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode
    )
    try:
        data = content.encode("utf-8")
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Create an Eidos skill.")
    parser.add_argument("skill_name")
    parser.add_argument("--path", help="Parent directory; defaults to Eidos user skills")
    parser.add_argument("--resources", default="", help="scripts,references")
    args = parser.parse_args(argv)

    name = re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", args.skill_name.lower())).strip("-")
    if not SKILL_NAME.fullmatch(name) or len(name) > 64:
        print("Error: skill name must be lowercase hyphen-case and at most 64 characters.", file=sys.stderr)
        return 1
    resources = [value.strip() for value in args.resources.split(",") if value.strip()]
    if set(resources) - RESOURCES:
        print("Error: resources must be scripts,references.", file=sys.stderr)
        return 1
    root = Path(args.path).expanduser() if args.path else _default_root()
    destination = root / name
    staging = root / f".create-{uuid.uuid4().hex}"
    try:
        _private_directory(root)
        if destination.exists() or (root / ".system" / name).exists():
            raise ValueError(f"skill already exists: {name}")
        staging.mkdir(mode=0o700)
        _write(staging / "SKILL.md", TEMPLATE.format(
            name=name, title=" ".join(part.capitalize() for part in name.split("-"))
        ))
        for resource in dict.fromkeys(resources):
            (staging / resource).mkdir(mode=0o700)
        os.replace(staging, destination)
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        print(f"Created {name} at {destination}")
        print("Edit SKILL.md, remove TODOs, then run quick_validate.py.")
        return 0
    except (OSError, ValueError) as error:
        shutil.rmtree(staging, ignore_errors=True)
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
