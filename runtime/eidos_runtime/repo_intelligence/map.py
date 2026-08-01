from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tomllib
from typing import Final

from pydantic import Field, model_validator

from eidos_runtime.models import EidosFrozenStrictModel, JsonSafeInt
from eidos_runtime.repo_intelligence.inventory import RepositoryInventory


MAX_MANIFEST_BYTES: Final = 256 * 1024


class DiscoveredCommand(EidosFrozenStrictModel):
    command: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class RepositoryMap(EidosFrozenStrictModel):
    schema_version: int = 1
    repository_id: str
    inventory_snapshot_id: str
    languages: tuple[str, ...]
    top_level_modules: tuple[str, ...]
    workspace_packages: tuple[str, ...]
    build_systems: tuple[str, ...]
    test_frameworks: tuple[str, ...]
    configuration_files: tuple[str, ...]
    entry_points: tuple[str, ...]
    source_roots: tuple[str, ...]
    test_roots: tuple[str, ...]
    generated_roots: tuple[str, ...]
    vendor_roots: tuple[str, ...]
    git_branch: str | None
    git_head: str | None
    commands: tuple[DiscoveredCommand, ...]
    created_at_ms: JsonSafeInt
    snapshot_id: str
    snapshot_hash: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def verify_hash(self) -> RepositoryMap:
        payload = self.model_dump(
            mode="json",
            exclude={"snapshot_id", "snapshot_hash", "created_at_ms"},
        )
        digest = _hash(payload)
        if digest != self.snapshot_hash or self.snapshot_id != f"map_{digest}":
            raise ValueError("repository map snapshot hash mismatch")
        return self


class RepositoryMapBuilder:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=True)

    def build(self, inventory: RepositoryInventory) -> RepositoryMap:
        if not inventory.complete or inventory.root != str(self.root):
            raise ValueError("complete matching inventory is required")
        file_paths = {record.path for record in inventory.files}
        languages = tuple(sorted({
            record.language for record in inventory.files if record.language is not None
        }))
        configuration_files = tuple(sorted(
            path for path in file_paths if Path(path).name in {
                "pyproject.toml", "package.json", "go.mod", "Cargo.toml",
                "Makefile", "pnpm-workspace.yaml", "tsconfig.json",
            } or path.startswith(".github/")
        ))
        source_roots = tuple(sorted(
            path for path in {
                *self._top_level_directories(inventory),
                *{
                    Path(record.path).parts[0]
                    for record in inventory.files
                    if record.language not in {None, "markdown", "json", "yaml"}
                },
            }
            if path in {"src", "app", "lib", "runtime", "cmd", "internal"}
        ))
        test_roots = tuple(sorted(
            path for path in self._top_level_directories(inventory)
            if path.lower() in {"test", "tests", "__tests__", "spec"}
        ))
        generated_roots = tuple(sorted({
            Path(record.path).parts[0]
            for record in inventory.files if record.generated
        }))
        vendor_roots = tuple(sorted({
            Path(record.path).parts[0]
            for record in inventory.files if record.vendor
        }))
        build_systems: set[str] = set()
        test_frameworks: set[str] = set()
        workspace_packages: set[str] = set()
        entry_points: set[str] = set()
        commands: list[DiscoveredCommand] = []
        for path in configuration_files:
            full_path = self.root / path
            try:
                raw = full_path.read_bytes()
                if len(raw) > MAX_MANIFEST_BYTES:
                    continue
            except OSError:
                continue
            if path == "pyproject.toml":
                self._read_pyproject(raw, build_systems, test_frameworks, commands, path)
            elif path == "package.json":
                self._read_package_json(raw, build_systems, test_frameworks, workspace_packages, commands, path)
            elif path == "go.mod":
                build_systems.add("go")
                commands.extend((
                    DiscoveredCommand(command="go test ./...", kind="test", source_path=path, confidence=0.85),
                    DiscoveredCommand(command="go build ./...", kind="build", source_path=path, confidence=0.8),
                ))
            elif path == "Cargo.toml":
                build_systems.add("cargo")
                commands.extend((
                    DiscoveredCommand(command="cargo test", kind="test", source_path=path, confidence=0.85),
                    DiscoveredCommand(command="cargo build", kind="build", source_path=path, confidence=0.8),
                ))
        for record in inventory.files:
            if Path(record.path).name in {"main.py", "main.ts", "index.ts", "main.go"}:
                entry_points.add(record.path)
        git_branch, git_head = _git_state(self.root)
        commands.sort(key=lambda command: (command.kind, command.command, command.source_path))
        payload = {
            "schema_version": 1,
            "repository_id": inventory.repository_id,
            "inventory_snapshot_id": inventory.snapshot_id,
            "languages": languages,
            "top_level_modules": tuple(self._top_level_directories(inventory)),
            "workspace_packages": tuple(sorted(workspace_packages)),
            "build_systems": tuple(sorted(build_systems)),
            "test_frameworks": tuple(sorted(test_frameworks)),
            "configuration_files": configuration_files,
            "entry_points": tuple(sorted(entry_points)),
            "source_roots": source_roots,
            "test_roots": test_roots,
            "generated_roots": generated_roots,
            "vendor_roots": vendor_roots,
            "git_branch": git_branch,
            "git_head": git_head,
            "commands": [command.model_dump(mode="json") for command in commands],
        }
        digest = _hash(payload)
        return RepositoryMap(
            schema_version=1,
            repository_id=inventory.repository_id,
            inventory_snapshot_id=inventory.snapshot_id,
            languages=languages,
            top_level_modules=tuple(self._top_level_directories(inventory)),
            workspace_packages=tuple(sorted(workspace_packages)),
            build_systems=tuple(sorted(build_systems)),
            test_frameworks=tuple(sorted(test_frameworks)),
            configuration_files=configuration_files,
            entry_points=tuple(sorted(entry_points)),
            source_roots=source_roots,
            test_roots=test_roots,
            generated_roots=generated_roots,
            vendor_roots=vendor_roots,
            git_branch=git_branch,
            git_head=git_head,
            commands=tuple(commands),
            created_at_ms=inventory.created_at_ms,
            snapshot_id=f"map_{digest}",
            snapshot_hash=digest,
        )

    def _top_level_directories(self, inventory: RepositoryInventory) -> tuple[str, ...]:
        return tuple(sorted({
            Path(record.path).parts[0]
            for record in inventory.files if len(Path(record.path).parts) > 1
        }))

    @staticmethod
    def _read_pyproject(raw: bytes, build_systems: set[str], test_frameworks: set[str], commands: list[DiscoveredCommand], source: str) -> None:
        try:
            document = tomllib.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError):
            return
        build_systems.add("python")
        tool = document.get("tool", {})
        if isinstance(tool, dict) and isinstance(tool.get("pytest"), dict):
            test_frameworks.add("pytest")
            commands.append(DiscoveredCommand(command="pytest", kind="test", source_path=source, confidence=0.8))

    @staticmethod
    def _read_package_json(raw: bytes, build_systems: set[str], test_frameworks: set[str], workspace_packages: set[str], commands: list[DiscoveredCommand], source: str) -> None:
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(document, dict):
            return
        build_systems.add("node")
        if isinstance(document.get("workspaces"), list):
            workspace_packages.update(
                item for item in document["workspaces"] if isinstance(item, str)
            )
        scripts = document.get("scripts")
        if not isinstance(scripts, dict):
            return
        for name, command in scripts.items():
            if not isinstance(name, str) or not isinstance(command, str):
                continue
            if name == "test":
                test_frameworks.add(command.split()[0] if command else "node")
                commands.append(DiscoveredCommand(command=command, kind="test", source_path=source, confidence=0.9))
            elif name in {"build", "compile"}:
                commands.append(DiscoveredCommand(command=command, kind="build", source_path=source, confidence=0.9))


def _git_state(root: Path) -> tuple[str | None, str | None]:
    try:
        branch = subprocess.run(
            ["git", "branch", "--show-current"], cwd=root, capture_output=True,
            text=True, timeout=0.5, check=False,
        ).stdout.strip() or None
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
            text=True, timeout=0.5, check=False,
        ).stdout.strip() or None
        return branch, head
    except (OSError, subprocess.SubprocessError):
        return None, None


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")).hexdigest()


__all__ = ["DiscoveredCommand", "RepositoryMap", "RepositoryMapBuilder"]
