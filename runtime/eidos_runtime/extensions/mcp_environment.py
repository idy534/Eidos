from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile


# Leave room for server-specific subdirectories and AF_UNIX socket names. Never
# derive this root from EIDOS_DATA_DIR or an inherited, possibly deep TMPDIR.
MCP_SOCKET_DIRECTORY_MAX_BYTES = 48


@dataclass(frozen=True)
class McpSocketDirectory:
    path: Path
    device: int
    inode: int
    owner: int

    @classmethod
    def create(cls) -> McpSocketDirectory:
        parent = Path("/private/tmp" if sys.platform == "darwin" else "/tmp").resolve(strict=True)
        path = Path(tempfile.mkdtemp(prefix="eidos-mcp-", dir=parent))
        identity = path.lstat()
        directory = cls(path, identity.st_dev, identity.st_ino, identity.st_uid)
        if (
            len(os.fsencode(path)) > MCP_SOCKET_DIRECTORY_MAX_BYTES
            or not stat.S_ISDIR(identity.st_mode)
            or identity.st_uid != os.getuid()
            or stat.S_IMODE(identity.st_mode) != 0o700
        ):
            directory.cleanup()
            raise OSError("mcp_temporary_directory_invalid")
        return directory

    def cleanup(self) -> None:
        try:
            identity = self.path.lstat()
        except FileNotFoundError:
            return
        if (
            not stat.S_ISDIR(identity.st_mode)
            or (identity.st_dev, identity.st_ino, identity.st_uid)
            != (self.device, self.inode, self.owner)
        ):
            raise OSError("mcp_temporary_directory_changed")
        # shutil's fd-based implementation does not follow substituted symlinks.
        shutil.rmtree(self.path)
