from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile
from unittest.mock import patch

import pytest

from eidos_runtime.extensions.mcp_environment import (
    MCP_SOCKET_DIRECTORY_MAX_BYTES,
    McpSocketDirectory,
)


def test_private_short_directory_ignores_inherited_temporary_root(tmp_path: Path) -> None:
    inherited = tmp_path / ("长目录" * 20)
    inherited.mkdir()
    with patch.dict(os.environ, {"TMPDIR": str(inherited)}):
        directory = McpSocketDirectory.create()
    try:
        identity = directory.path.lstat()
        assert len(os.fsencode(directory.path)) <= MCP_SOCKET_DIRECTORY_MAX_BYTES
        assert directory.path == directory.path.resolve(strict=True)
        assert stat.S_IMODE(identity.st_mode) == 0o700
        assert identity.st_uid == os.getuid()
        assert not directory.path.is_relative_to(inherited)
    finally:
        directory.cleanup()
    assert not directory.path.exists()
    directory.cleanup()


def test_connections_never_share_temporary_directories() -> None:
    first = McpSocketDirectory.create()
    second = McpSocketDirectory.create()
    try:
        assert first.path != second.path
        (second.path / "active").write_text("second")
        first.cleanup()
        assert (second.path / "active").read_text() == "second"
    finally:
        first.cleanup()
        second.cleanup()


def test_cleanup_does_not_follow_links_inside_owned_directory(tmp_path: Path) -> None:
    protected = tmp_path / "protected"
    protected.mkdir()
    (protected / "keep").write_text("unchanged")
    directory = McpSocketDirectory.create()
    (directory.path / "nested").mkdir()
    (directory.path / "nested" / "result").write_text("temporary")
    (directory.path / "outside").symlink_to(protected, target_is_directory=True)
    directory.cleanup()
    assert (protected / "keep").read_text() == "unchanged"
    assert not directory.path.exists()


@pytest.mark.parametrize("replacement", ["symlink", "directory"])
def test_cleanup_refuses_replaced_directory(tmp_path: Path, replacement: str) -> None:
    directory = McpSocketDirectory.create()
    displaced = directory.path.with_name(directory.path.name + "-displaced")
    directory.path.rename(displaced)
    protected = tmp_path / "protected"
    protected.mkdir()
    (protected / "keep").write_text("unchanged")
    try:
        if replacement == "symlink":
            directory.path.symlink_to(protected, target_is_directory=True)
        else:
            directory.path.mkdir()
            (directory.path / "keep").write_text("replacement")
        with pytest.raises(OSError, match="mcp_temporary_directory_changed"):
            directory.cleanup()
        assert (protected / "keep").read_text() == "unchanged"
        if replacement == "directory":
            assert (directory.path / "keep").read_text() == "replacement"
    finally:
        if replacement == "symlink":
            directory.path.unlink()
        else:
            (directory.path / "keep").unlink()
            directory.path.rmdir()
        displaced.rename(directory.path)
        directory.cleanup()


def test_rejects_long_canonical_temporary_root_without_leaving_directory(tmp_path: Path) -> None:
    long_root = tmp_path / ("临时目录" * 12)
    long_root.mkdir()
    mkdtemp = tempfile.mkdtemp
    with patch(
        "eidos_runtime.extensions.mcp_environment.tempfile.mkdtemp",
        side_effect=lambda **kwargs: mkdtemp(prefix=kwargs["prefix"], dir=long_root),
    ):
        with pytest.raises(OSError, match="mcp_temporary_directory_invalid"):
            McpSocketDirectory.create()
    assert list(long_root.iterdir()) == []
