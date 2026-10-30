"""Readable Projectless directory names and fd-relative directory creation."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import os
from pathlib import Path
import stat
import unicodedata
from typing import Iterator

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
    text = unicodedata.normalize("NFKC", (hint or "")[:MAX_NAME_HINT_CHARACTERS])
    text = "".join(
        " " if unicodedata.category(char).startswith("C") or char in '/\\:*?"<>|'
        else char
        for char in text
    )
    text = "-".join(text.split()).strip(". -_`'“”‘’")
    text = text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
    return text.rstrip(". -") or "新会话"


def creation_date(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000).astimezone().date().isoformat()


@contextmanager
def _directory(path: Path, *, create: bool = False) -> Iterator[int]:
    """Open canonical trusted root components without following replacements."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                    os.fsync(descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        metadata = os.fstat(descriptor)
        if metadata.st_uid != os.getuid():
            raise WorkspacePathError("workspace_unavailable")
        yield descriptor
    finally:
        os.close(descriptor)


class ProjectlessDirectoryAllocator:
    def create(self, path: Path) -> WorkspaceIdentity:
        with _directory(path.parent, create=True) as parent:
            os.mkdir(path.name, mode=0o700, dir_fd=parent)
            os.fsync(parent)
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            descriptor = os.open(path.name, flags, dir_fd=parent)
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
            metadata = os.stat(identity.path.name, dir_fd=parent, follow_symlinks=False)
            if (metadata.st_dev, metadata.st_ino, metadata.st_uid) != (
                identity.device, identity.inode, identity.owner
            ):
                raise WorkspacePathError("workspace_identity_changed")
            os.rmdir(identity.path.name, dir_fd=parent)
            os.fsync(parent)
