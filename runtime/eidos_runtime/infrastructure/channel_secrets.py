from __future__ import annotations

import os
from pathlib import Path
import stat
from uuid import UUID, uuid4

from eidos_runtime.domain.channels import ChannelError


class ChannelSecrets:
    """Bounded owner-only files, outside SQLite and protocol responses."""

    def __init__(self, data_directory: Path) -> None:
        self.directory = data_directory / "channel-secrets"

    def _directory(self) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ChannelError("渠道凭证目录的权限无效。")

    def write(self, secret: str) -> str:
        self._directory()
        identifier = str(uuid4())
        descriptor = os.open(self.directory / identifier, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(secret.encode())
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            (self.directory / identifier).unlink(missing_ok=True)
            raise
        return identifier

    def read(self, identifier: str) -> str:
        try:
            if str(UUID(identifier)) != identifier:
                raise ValueError
            self._directory()
            descriptor = os.open(self.directory / identifier, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077:
                    raise ValueError
                raw = stream.read(1025)
            if not raw or len(raw) > 1024:
                raise ValueError
            return raw.decode()
        except (OSError, ValueError, UnicodeError) as error:
            raise ChannelError("渠道凭证不可用，请重新保存 Client Secret。") from error
