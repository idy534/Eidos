"""Readable Projectless directory names and fd-relative directory creation."""

from __future__ import annotations

from eidos_runtime.infrastructure import local_files

from contextlib import contextmanager
from datetime import datetime
import os
from pathlib import Path
import stat
import unicodedata
from typing import Iterator

from eidos_runtime.context.input_references import INLINE_REFERENCE_TOKEN
from eidos_runtime.db.database import WorkspaceIdentity
from eidos_runtime.workspace.reader import WorkspacePathError, capture_directory_identity

MAX_NAME_BYTES = 96
MAX_NEW_NAME_BYTES = 48
MAX_NAME_HINT_CHARACTERS = 512


def projectless_root() -> Path:
    configured = os.environ.get("EIDOS_PROJECTLESS_ROOT")
    root = Path(configured) if configured else Path.home() / "Eidos"
    if not root.is_absolute() or root.is_symlink():
        raise WorkspacePathError("workspace_unavailable")
    return root.resolve(strict=False)


def readable_workspace_name(hint: str | None, *, max_bytes: int = MAX_NAME_BYTES) -> str:
    """Return a directory name that a shell can use without quoting.

    Letters and digits from any script stay, as do ``_`` and combining marks on
    a kept character. Every other character (punctuation, symbols, spaces,
    controls) separates words with one ``-``. Composer references keep only
    their label, so the name never carries ``$``, brackets or reference IDs.
    """
    text = unicodedata.normalize("NFKC", (hint or "")[:MAX_NAME_HINT_CHARACTERS])
    text = INLINE_REFERENCE_TOKEN.sub(lambda match: f" {match[1]} ", text)
    characters: list[str] = []
    attached = False
    for char in text:
        category = unicodedata.category(char)
        attached = (
            char == "_" or category[0] in "LN"
            or (attached and category in {"Mn", "Mc"} and not _is_variation_selector(char))
        )
        characters.append(char if attached else " ")
    text = "-".join("".join(characters).split())
    text = text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
    return text.strip("-_") or "新会话"


def legacy_readable_workspace_name(hint: str | None, *, max_bytes: int = MAX_NAME_BYTES) -> str:
    """Name rule of earlier versions, which kept shell punctuation.

    Existing creation records and operation replays still use these names.
    """
    text = unicodedata.normalize("NFKC", (hint or "")[:MAX_NAME_HINT_CHARACTERS])
    text = "".join(
        " " if unicodedata.category(char).startswith("C") or char in '/\\:*?"<>|'
        else char
        for char in text
    )
    text = "-".join(text.split()).strip(". -_`'“”‘’")
    text = text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
    return text.rstrip(". -") or "新会话"


def is_readable_workspace_name(name: str) -> bool:
    return name in {readable_workspace_name(name), legacy_readable_workspace_name(name)}


def _is_variation_selector(char: str) -> bool:
    code = ord(char)
    return 0xFE00 <= code <= 0xFE0F or 0xE0100 <= code <= 0xE01EF


def creation_date(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000).astimezone().date().isoformat()


@contextmanager
def _directory(path: Path, *, create: bool = False) -> Iterator[int]:
    """Open canonical trusted root components without following replacements."""
    descriptor = local_files.open_directory(path.anchor)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    local_files.mkdir_at(part, descriptor, 0o700)
                    local_files.sync_directory(descriptor)
                except FileExistsError:
                    pass
            child = local_files.open_directory(part, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        metadata = os.fstat(descriptor)
        if not local_files.owned_by_user(descriptor, metadata):
            raise WorkspacePathError("workspace_unavailable")
        yield descriptor
    finally:
        os.close(descriptor)


class ProjectlessDirectoryAllocator:
    def create(self, path: Path) -> WorkspaceIdentity:
        with _directory(path.parent, create=True) as parent:
            local_files.mkdir_at(path.name, parent, 0o700)
            local_files.sync_directory(parent)
            descriptor = local_files.open_directory(path.name, dir_fd=parent)
            try:
                metadata = os.fstat(descriptor)
                identity = WorkspaceIdentity(
                    path=path, device=metadata.st_dev, inode=metadata.st_ino,
                    owner=metadata.st_uid,
                )
                self.verify(identity)
                return identity
            finally:
                os.close(descriptor)

    def verify(self, identity: WorkspaceIdentity) -> None:
        with _directory(identity.path) as descriptor:
            metadata = os.fstat(descriptor)
            if not stat.S_ISDIR(metadata.st_mode) or (
                metadata.st_dev, metadata.st_ino, metadata.st_uid
            ) != (identity.device, identity.inode, identity.owner):
                raise WorkspacePathError("workspace_identity_changed")
        if capture_directory_identity(identity.path) != identity:
            raise WorkspacePathError("workspace_identity_changed")

    def remove_empty(self, identity: WorkspaceIdentity) -> None:
        self.verify(identity)
        with _directory(identity.path.parent) as parent:
            metadata = local_files.lstat_at(identity.path.name, parent)
            if (metadata.st_dev, metadata.st_ino, metadata.st_uid) != (
                identity.device, identity.inode, identity.owner
            ):
                raise WorkspacePathError("workspace_identity_changed")
            local_files.rmdir_at(identity.path.name, parent)
            local_files.sync_directory(parent)
