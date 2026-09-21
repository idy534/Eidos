"""Skill management outside the immutable Run catalog."""
from __future__ import annotations

import base64
import hashlib
import logging
import os
from pathlib import Path
import shutil
import stat

from eidos_runtime.extensions.skills import (
    MAX_SKILL_BYTES, SkillCatalog, SkillReadError, _SkillSource,
    _read_text, _read_tree, _scan, _tree_hash,
)
from eidos_runtime.extensions.skill_manifest import parse_skill_manifest
from eidos_runtime.models.skill_settings import (
    ManagedSkill, SkillDetail, SkillRemoval, SkillState,
)

logger = logging.getLogger(__name__)

MAX_ICON_BYTES = 256 * 1024


def _read_skill_icon(skill_root: Path, skill_name: str) -> str | None:
    candidates = [
        (f"{skill_name}-small.svg", "image/svg+xml"),
        (f"{skill_name}.png", "image/png"),
    ]
    subdirs = ["assets", "assents"]
    for filename, mime_type in candidates:
        for subdir in subdirs:
            candidate_path = skill_root / subdir / filename
            try:
                stat_result = candidate_path.lstat()
                if stat.S_ISLNK(stat_result.st_mode) or not stat.S_ISREG(stat_result.st_mode):
                    continue
                if stat_result.st_size == 0 or stat_result.st_size > MAX_ICON_BYTES:
                    continue
                canonical_root = skill_root.resolve(strict=True)
                canonical_candidate = candidate_path.resolve(strict=True)
                if not canonical_candidate.is_relative_to(canonical_root):
                    continue
                data = candidate_path.read_bytes()
                encoded = base64.b64encode(data).decode("ascii")
                return f"data:{mime_type};base64,{encoded}"
            except (OSError, ValueError):
                continue
    return None


class SkillManagement:
    def __init__(self, catalog: SkillCatalog) -> None:
        self.catalog = catalog
        self.store = catalog.plugins.store

    def _sources(self) -> list[_SkillSource]:
        return self.catalog._sources(
            self.catalog.plugins.extension_snapshot(include_disabled=True)
        )

    def _source(self, qualified_id: str) -> _SkillSource:
        if any(s.qualified_id == qualified_id and s.removed for s in self.store.skill_states()):
            raise SkillReadError("skill_unavailable")
        for source in self._sources():
            if source.qualified_id == qualified_id:
                return source
        raise SkillReadError("skill_unavailable")

    def list(self) -> tuple[ManagedSkill, ...]:
        self.cleanup()
        states = {state.qualified_id: state for state in self.store.skill_states()}
        enabled_plugins = {
            str(plugin["id"]) for plugin in self.catalog.plugins.list_plugins()
            if plugin["enabled"]
        }
        result = []
        for source in self._sources():
            state = states.get(source.qualified_id)
            if state and state.removed:
                continue
            available = source.source_kind != "plugin" or source.source_id in enabled_plugins
            icon = _read_skill_icon(source.root, source.name)
            result.append(ManagedSkill(
                qualified_id=source.qualified_id, name=source.name,
                description=source.description, plugin_id=source.source_id,
                plugin_version=source.source_version, plugin_hash=source.source_hash,
                content_hash=source.content_hash, source_kind=source.source_kind,
                enabled=state.enabled if state else True, available=available,
                icon=icon,
            ))
        return tuple(result)

    def detail(self, qualified_id: str) -> SkillDetail:
        source = self._source(qualified_id)
        content = _scan(_read_text(source.root / "SKILL.md", MAX_SKILL_BYTES))
        # Use the already validated frontmatter delimiter, preserving the raw file for copying.
        lines = content.splitlines(keepends=True)
        body = content
        if lines and lines[0].strip() == "---":
            for index, line in enumerate(lines[1:], 1):
                if line.strip() in {"---", "..."}:
                    body = "".join(lines[index + 1:]).lstrip("\r\n")
                    break
        return SkillDetail(
            qualified_id=qualified_id, content=content, body=body,
            directory=str(source.root.resolve(strict=True)),
        )

    def set_enabled(self, qualified_id: str, enabled: bool) -> ManagedSkill:
        source = self._source(qualified_id)
        self.store.save_skill_state(SkillState(
            qualified_id=qualified_id, enabled=enabled, source_kind=source.source_kind,
            directory_name=source.root.name if source.source_kind == "user" else None,
            content_hash=source.content_hash,
        ))
        return next(skill for skill in self.list() if skill.qualified_id == qualified_id)

    def remove(self, qualified_id: str) -> SkillRemoval:
        prior = next((s for s in self.store.skill_states() if s.qualified_id == qualified_id), None)
        if prior and prior.removed:
            self.cleanup()
        else:
            source = self._source(qualified_id)
            if source.source_kind == "system":
                raise SkillReadError("system_skill_protected")
            identity = source.root.lstat()
            self.store.save_skill_state(SkillState(
                qualified_id=qualified_id, enabled=False, removed=True,
                source_kind=source.source_kind,
                directory_name=source.root.name if source.source_kind == "user" else None,
                directory_device=identity.st_dev, directory_inode=identity.st_ino,
                content_hash=source.source_hash,
                cleanup_pending=source.source_kind == "user",
            ))
            self.cleanup()
        state = next(s for s in self.store.skill_states() if s.qualified_id == qualified_id)
        return SkillRemoval(qualified_id=qualified_id, cleanup_pending=state.cleanup_pending)

    def cleanup(self) -> None:
        # ponytail: retain files while any Run is active; track individual roots if cleanup latency matters.
        with self.store.lock:
            if self.store.has_nonterminal_runs() or self.store.data_directory is None:
                return
            root = self.store.data_directory / "skills"
            for state in self.store.skill_states():
                if not state.removed or not state.cleanup_pending or state.source_kind != "user":
                    continue
                name = state.directory_name
                if not name or name.startswith(".") or Path(name).name != name:
                    continue
                try:
                    # The deterministic private staging name lets restart finish a partial removal.
                    suffix = hashlib.sha256(
                        f"{state.qualified_id}:{state.content_hash}:{state.directory_inode}".encode()
                    ).hexdigest()
                    staged_name = f".skill-removed-{suffix}"
                    parent_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try:
                        parent = os.fstat(parent_fd)
                        if parent.st_uid != os.getuid():
                            continue
                        staged = root / staged_name
                        if not os.path.lexists(staged):
                            if not os.path.lexists(root / name):
                                self.store.save_skill_state(state.model_copy(update={"cleanup_pending": False}))
                                continue
                            target = root / name
                            identity = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                            if not self._same_directory(identity, state):
                                continue
                            files = _read_tree(target, owned=True)
                            manifest = parse_skill_manifest(files["SKILL.md"].decode("utf-8"), name)
                            if f"user:{manifest.name}" != state.qualified_id or _tree_hash(files) != state.content_hash:
                                continue
                            identity = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                            if not self._same_directory(identity, state):
                                continue
                            os.rename(name, staged_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                            os.fsync(parent_fd)
                        identity = os.stat(staged_name, dir_fd=parent_fd, follow_symlinks=False)
                        if not self._same_directory(identity, state):
                            continue
                        shutil.rmtree(staged_name, dir_fd=parent_fd)
                        os.fsync(parent_fd)
                    finally:
                        os.close(parent_fd)
                    self.store.save_skill_state(state.model_copy(update={"cleanup_pending": False}))
                except (OSError, ValueError, KeyError):
                    logger.warning("skill_cleanup_deferred")

    @staticmethod
    def _same_directory(identity: os.stat_result, state: SkillState) -> bool:
        return (
            stat.S_ISDIR(identity.st_mode) and identity.st_uid == os.getuid()
            and (identity.st_dev, identity.st_ino) == (state.directory_device, state.directory_inode)
        )
