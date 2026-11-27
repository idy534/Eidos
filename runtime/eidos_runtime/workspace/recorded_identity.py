"""Match directory identities that an earlier Runtime process recorded."""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import sys
from typing import TYPE_CHECKING

from eidos_runtime.domain.project_workspace import DirectoryIdentity, SessionRoot

if TYPE_CHECKING:
    from eidos_runtime.db.database import WorkspaceIdentity


# macOS takes st_dev from the /dev/disk node it creates when a volume mounts,
# so the same APFS volume can report another number after a restart or a
# remount. Windows reports the volume serial number, which stays the same.
_DEVICE_NUMBERS_CHANGE_ON_REMOUNT = sys.platform == "darwin"

FileIdentity = tuple[int, int, int]
"""A directory's ``(device, inode, owner)``."""


def same_recorded_directory(recorded: FileIdentity, observed: FileIdentity) -> bool:
    """Return whether an observed directory is the one a durable record names.

    The inode and owner must match. The device number must match too, except on
    platforms that renumber a volume each time it mounts.
    """
    if recorded == observed:
        return True
    return _DEVICE_NUMBERS_CHANGE_ON_REMOUNT and recorded[1:] == observed[1:]


def current_device(path: str | Path, recorded: FileIdentity) -> int:
    """Return the device number to expect now for a recorded directory.

    The recorded number is kept when the path is unavailable, passes through a
    symlink, or names another directory, so the existing checks still reject
    it with their own error codes.
    """
    device = recorded[0]
    if not _DEVICE_NUMBERS_CHANGE_ON_REMOUNT:
        return device
    try:
        if os.stat(path, follow_symlinks=False).st_dev == device:
            return device
    except (OSError, ValueError):
        return device
    from eidos_runtime.workspace.reader import (
        WorkspacePathError,
        capture_directory_identity,
    )

    try:
        observed = capture_directory_identity(path)
    except WorkspacePathError:
        return device
    if not same_recorded_directory(
        recorded, (observed.device, observed.inode, observed.owner)
    ):
        return device
    return observed.device


def current_directory_identity(
    path: str | Path | None, recorded: DirectoryIdentity | None
) -> DirectoryIdentity | None:
    if path is None or recorded is None:
        return recorded
    device = current_device(path, (recorded.device, recorded.inode, recorded.owner))
    if device == recorded.device:
        return recorded
    return recorded.model_copy(update={"device": device})


def current_session_root(root: SessionRoot) -> SessionRoot:
    device = current_device(root.path, (root.device, root.inode, root.owner))
    git_dir_identity = current_directory_identity(root.git_dir, root.git_dir_identity)
    git_common_identity = current_directory_identity(
        root.git_common_dir, root.git_common_identity
    )
    if (device, git_dir_identity, git_common_identity) == (
        root.device, root.git_dir_identity, root.git_common_identity,
    ):
        return root
    return root.model_copy(update={
        "device": device,
        "git_dir_identity": git_dir_identity,
        "git_common_identity": git_common_identity,
    })


def current_workspace_identity(identity: WorkspaceIdentity) -> WorkspaceIdentity:
    return replace(
        identity,
        device=current_device(
            identity.path, (identity.device, identity.inode, identity.owner)
        ),
        git_dir_identity=current_directory_identity(
            identity.git_dir, identity.git_dir_identity
        ),
        git_common_identity=current_directory_identity(
            identity.git_common_dir, identity.git_common_identity
        ),
        additional_roots=tuple(
            current_workspace_identity(root) for root in identity.additional_roots
        ),
    )


__all__ = [
    "current_device",
    "current_directory_identity",
    "current_session_root",
    "current_workspace_identity",
    "same_recorded_directory",
]
