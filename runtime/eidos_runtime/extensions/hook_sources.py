"""Discover user/project Hook files and bind review to their exact content."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import tomllib

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.hooks import HookBinding, HookSourceReview
from eidos_runtime.extensions.codex_hooks import parse_hooks
from eidos_runtime.persistence.hooks import HookRepository


class HookSourceCatalog:
    def __init__(self, store: SessionStore) -> None:
        self.store = store
        self.repository = HookRepository(store.database)

    def discover(self, workspace: Path | None = None) -> tuple[HookSourceReview, ...]:
        assert self.store.data_directory is not None
        locations = [("user", self.store.data_directory)]
        if workspace is not None:
            locations.append(("project", workspace / ".eidos"))
        sources = []
        for scope, directory in locations:
            paths = tuple(path for name in ("hooks.json", "config.toml") if os.path.lexists(path := directory / name))
            if not paths:
                continue
            source_id = scope + ":" + hashlib.sha256(str(directory).encode()).hexdigest()[:24]
            definitions, digest = [], hashlib.sha256()
            error_code = None
            try:
                for path in paths:
                    raw = _read(path)
                    digest.update(path.name.encode() + b"\0" + raw + b"\0")
                    if path.suffix == ".toml":
                        value = tomllib.loads(raw.decode("utf-8"))
                        value = {"hooks": value.get("hooks", {})}
                    else:
                        from eidos_runtime.extensions.plugin_package import unique_object
                        value = json.loads(raw, object_pairs_hook=unique_object)
                    definitions.extend(parse_hooks(value, path.name))
                if len(definitions) > 128:
                    raise ValueError("hook_capacity_exceeded")
            except (OSError, ValueError, RecursionError):
                error_code = "hook_source_invalid"
                definitions = []
            source = HookSourceReview(id=source_id, scope=scope, paths=tuple(map(str, paths)),
                content_hash=digest.hexdigest(), definitions=tuple(definitions), error_code=error_code)
            sources.append(self.repository.save_source(source))
        return tuple(sources)

    def bindings(self, workspace: Path | None = None) -> tuple[HookBinding, ...]:
        return tuple(HookBinding(plugin_id=source.id, plugin_hash=source.content_hash,
            definition=definition, source_kind=source.scope, source_paths=source.paths)
            for source in self.discover(workspace) if source.enabled and source.trusted
            for definition in source.definitions if definition.supported)

    def verify(self, binding: HookBinding) -> Path:
        parent = verify_source_paths(binding.source_paths, binding.plugin_hash)
        # User scripts receive access to their dedicated directory, never all data.
        return parent / "hooks" if binding.source_kind == "user" else parent


def verify_source_paths(paths: tuple[str, ...], content_hash: str) -> Path:
    digest = hashlib.sha256()
    for value in paths:
        path = Path(value)
        digest.update(path.name.encode() + b"\0" + _read(path) + b"\0")
    if not paths or digest.hexdigest() != content_hash:
        raise ValueError("hook_source_changed")
    return Path(paths[0]).parent


def _read(path: Path) -> bytes:
    if path.parent.is_symlink():
        raise ValueError("hook_source_invalid")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                or before.st_nlink != 1 or before.st_size > 65536):
            raise ValueError("hook_source_invalid")
        raw = os.read(descriptor, 65537)
        after = os.fstat(descriptor)
        if len(raw) > 65536 or (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise ValueError("hook_source_changed")
        return raw
    finally:
        os.close(descriptor)
