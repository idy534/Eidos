from __future__ import annotations

import hashlib
import os
import stat
import threading
import time
from typing import Callable

from pydantic import BaseModel, ConfigDict

from eidos_runtime.db.database import WorkspaceIdentity
from eidos_runtime.sandbox.workspace_manifest import (
    HASH_LIMIT_BYTES,
    IGNORED_NAMES,
    WorkspaceManifest,
    WorkspaceManifestEntry,
)


class WorkspaceIndexIncomplete(RuntimeError):
    pass


class WorkspaceIdentitySnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    path: str
    device: int
    inode: int
    owner: int


class WorkspaceIndexSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    workspace_identity: WorkspaceIdentitySnapshot
    generation: int
    complete: bool
    scanned_at: int
    entry_count: int
    sensitive_entries: tuple[str, ...] = ()


EntryValidator = Callable[[int, str, os.stat_result, str, bool], None]
DirectoryOpener = Callable[[int, str], int]


class WorkspaceIndex:
    """Run-lifetime security scan and manifest cache."""

    def __init__(self, identity: WorkspaceIdentity) -> None:
        self.identity = identity
        self._lock = threading.RLock()
        self._generation = 0
        self._entries: tuple[WorkspaceManifestEntry, ...] = ()
        self._fingerprints: dict[
            str, tuple[int, int, int, int, int, int]
        ] = {}
        self._digests: dict[str, str | None] = {}
        self._snapshot = WorkspaceIndexSnapshot(
            workspace_identity=WorkspaceIdentitySnapshot(
                path=str(identity.path),
                device=identity.device,
                inode=identity.inode,
                owner=identity.owner,
            ),
            generation=0,
            complete=False,
            scanned_at=0,
            entry_count=0,
        )

    @property
    def snapshot(self) -> WorkspaceIndexSnapshot:
        with self._lock:
            return self._snapshot

    def manifest(self) -> WorkspaceManifest:
        with self._lock:
            return WorkspaceManifest(
                self._entries,
                self._snapshot.complete,
                not self._snapshot.complete,
            )

    def refresh(
        self,
        root_fd: int,
        cancel: threading.Event,
        *,
        validate: EntryValidator,
        open_directory: DirectoryOpener,
        deadline: float,
    ) -> WorkspaceIndexSnapshot:
        entries: list[WorkspaceManifestEntry] = []
        fingerprints: dict[
            str, tuple[int, int, int, int, int, int]
        ] = {}
        digests: dict[str, str | None] = {}
        entry_count = 0

        def visit(
            directory_fd: int,
            relative_parent: str,
            manifest_ignored: bool,
        ) -> None:
            nonlocal entry_count
            try:
                with os.scandir(directory_fd) as scanned:
                    children = sorted(
                        scanned,
                        key=lambda entry: os.fsencode(entry.name),
                    )
            except OSError:
                raise WorkspaceIndexIncomplete from None
            for child in children:
                if cancel.is_set():
                    raise WorkspaceIndexIncomplete
                if time.monotonic() >= deadline:
                    raise WorkspaceIndexIncomplete
                name = child.name
                relative = (
                    f"{relative_parent}/{name}"
                    if relative_parent
                    else name
                )
                try:
                    metadata = os.stat(
                        name,
                        dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                except OSError:
                    raise WorkspaceIndexIncomplete from None
                entry_count += 1
                is_git = not relative_parent and name == ".git"
                validate(
                    directory_fd,
                    name,
                    metadata,
                    relative,
                    is_git,
                )
                if is_git:
                    continue
                ignored = manifest_ignored or name in IGNORED_NAMES
                if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(
                    metadata.st_mode
                ):
                    child_fd = open_directory(directory_fd, name)
                    try:
                        visit(child_fd, relative, ignored)
                    finally:
                        os.close(child_fd)
                    continue
                if ignored:
                    continue
                fingerprint = (
                    metadata.st_dev,
                    metadata.st_ino,
                    metadata.st_size,
                    metadata.st_mtime_ns,
                    stat.S_IMODE(metadata.st_mode),
                    metadata.st_nlink,
                )
                digest = self._cached_digest(
                    directory_fd,
                    name,
                    relative,
                    metadata,
                    fingerprint,
                    deadline,
                )
                fingerprints[relative] = fingerprint
                digests[relative] = digest
                entries.append(WorkspaceManifestEntry(
                    relative,
                    metadata.st_size,
                    metadata.st_mtime_ns,
                    stat.S_IMODE(metadata.st_mode),
                    metadata.st_ino or None,
                    digest,
                ))

        descriptor = os.dup(root_fd)
        try:
            visit(descriptor, "", False)
        except WorkspaceIndexIncomplete:
            with self._lock:
                self._snapshot = self._snapshot.model_copy(
                    update={
                        "complete": False,
                        "scanned_at": int(time.time() * 1000),
                        "entry_count": entry_count,
                    }
                )
            raise
        finally:
            os.close(descriptor)

        ordered = tuple(
            sorted(entries, key=lambda entry: os.fsencode(entry.path))
        )
        with self._lock:
            if fingerprints != self._fingerprints:
                self._generation += 1
            self._entries = ordered
            self._fingerprints = fingerprints
            self._digests = digests
            self._snapshot = self._snapshot.model_copy(
                update={
                    "generation": self._generation,
                    "complete": True,
                    "scanned_at": int(time.time() * 1000),
                    "entry_count": entry_count,
                }
            )
            return self._snapshot

    def _cached_digest(
        self,
        directory_fd: int,
        name: str,
        relative: str,
        metadata: os.stat_result,
        fingerprint: tuple[int, int, int, int, int, int],
        deadline: float,
    ) -> str | None:
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > HASH_LIMIT_BYTES
        ):
            return None
        with self._lock:
            if self._fingerprints.get(relative) == fingerprint:
                return self._digests.get(relative)
        flags = os.O_RDONLY | os.O_NONBLOCK
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(name, flags, dir_fd=directory_fd)
            digest = hashlib.sha256()
            while chunk := os.read(descriptor, 64 * 1024):
                if time.monotonic() >= deadline:
                    raise WorkspaceIndexIncomplete
                digest.update(chunk)
            after = os.fstat(descriptor)
        except OSError:
            raise WorkspaceIndexIncomplete from None
        finally:
            if "descriptor" in locals():
                os.close(descriptor)
        if (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) != (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
        ):
            raise WorkspaceIndexIncomplete
        return digest.hexdigest()
