from __future__ import annotations

from collections.abc import Callable
from itertools import islice
import os
from pathlib import Path
import stat
from uuid import UUID, uuid4


class SecretFiles:
    """Bounded owner-only files, outside SQLite and protocol responses."""

    def __init__(self, directory: Path, *, max_bytes: int = 1024) -> None:
        self.directory = directory
        self.max_bytes = max_bytes

    def _directory(self) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.directory.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise ValueError("credential directory permissions invalid")

    def write(self, secret: str) -> str:
        self._directory()
        identifier = str(uuid4())
        descriptor = os.open(
            self.directory / identifier,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(secret.encode())
                stream.flush()
                os.fsync(stream.fileno())
            directory_fd = os.open(
                self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except BaseException:
            (self.directory / identifier).unlink(missing_ok=True)
            raise
        return identifier

    def read(self, identifier: str) -> str:
        try:
            if str(UUID(identifier)) != identifier:
                raise ValueError
            self._directory()
            descriptor = os.open(
                self.directory / identifier, os.O_RDONLY | os.O_NOFOLLOW
            )
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or info.st_uid != os.getuid()
                    or info.st_mode & 0o077
                ):
                    raise ValueError
                raw = stream.read(self.max_bytes + 1)
            if not raw or len(raw) > self.max_bytes:
                raise ValueError
            return raw.decode()
        except (OSError, ValueError, UnicodeError) as error:
            raise ValueError("credential unavailable") from error

    def prune(self, retained: Callable[[str], bool]) -> None:
        """Bound each cleanup pass and retain current/nonterminal Run versions."""
        self._directory()
        with os.scandir(self.directory) as entries:
            for entry in islice(entries, 256):
                try:
                    if str(UUID(entry.name)) == entry.name and not retained(entry.name):
                        os.unlink(entry.path)
                except (OSError, ValueError):
                    continue
