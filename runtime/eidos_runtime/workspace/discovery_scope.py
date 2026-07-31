from __future__ import annotations

from dataclasses import dataclass
import errno
import os
import stat

from pathspec import GitIgnoreSpec


MAX_IGNORE_FILE_BYTES = 256 * 1024
MAX_IGNORE_PATTERNS = 10_000
_ROOT_IGNORE_FILES = (".gitignore", ".eidosignore")


class DiscoveryScopeError(ValueError):
    """A bounded root ignore file cannot safely define discovery scope."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class WorkspaceDiscoveryScope:
    """Git-style ignore matching for ordinary repository discovery only."""

    spec: GitIgnoreSpec

    @classmethod
    def load(cls, root_fd: int) -> WorkspaceDiscoveryScope:
        patterns: list[str] = []
        for name in _ROOT_IGNORE_FILES:
            patterns.extend(_read_root_ignore_file(root_fd, name))
            if len(patterns) > MAX_IGNORE_PATTERNS:
                raise DiscoveryScopeError("ignore_file_too_many_patterns")
        return cls(GitIgnoreSpec.from_lines(patterns))

    def is_ignored(self, relative_path: str, *, is_directory: bool) -> bool:
        normalized = _normalize_relative_path(relative_path)
        candidate = f"{normalized}/" if is_directory else normalized
        return self.spec.match_file(candidate)


def _read_root_ignore_file(root_fd: int, name: str) -> list[str]:
    flags = os.O_RDONLY | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(name, flags, dir_fd=root_fd)
    except FileNotFoundError:
        return []
    except OSError as error:
        if error.errno == errno.ENOENT:
            return []
        raise DiscoveryScopeError("ignore_file_invalid") from None
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise DiscoveryScopeError("ignore_file_invalid")
        if before.st_size > MAX_IGNORE_FILE_BYTES:
            raise DiscoveryScopeError("ignore_file_too_large")
        content = _read_bounded(descriptor)
        after = os.fstat(descriptor)
    except OSError:
        raise DiscoveryScopeError("ignore_file_invalid") from None
    finally:
        os.close(descriptor)
    if len(content) > MAX_IGNORE_FILE_BYTES:
        raise DiscoveryScopeError("ignore_file_too_large")
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_nlink,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_nlink,
    ) or len(content) != before.st_size:
        raise DiscoveryScopeError("ignore_file_changed")
    try:
        text = content.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError:
        raise DiscoveryScopeError("ignore_file_invalid_utf8") from None
    patterns = text.splitlines()
    if len(patterns) > MAX_IGNORE_PATTERNS:
        raise DiscoveryScopeError("ignore_file_too_many_patterns")
    return patterns


def _read_bounded(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    remaining = MAX_IGNORE_FILE_BYTES + 1
    while remaining:
        chunk = os.read(descriptor, min(64 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _normalize_relative_path(relative_path: str) -> str:
    if not relative_path or relative_path.startswith("/") or "\\" in relative_path:
        raise DiscoveryScopeError("invalid_discovery_path")
    parts = relative_path.split("/")
    if any(part in {"", ".", ".."} or "\x00" in part for part in parts):
        raise DiscoveryScopeError("invalid_discovery_path")
    return "/".join(parts)
