from __future__ import annotations

import os
from pathlib import Path
import stat

from eidos_runtime.tools.workspace import (
    ToolExecutor as BaseToolExecutor,
    WorkspacePathError,
    _contains_sensitive_pem,
    _is_sensitive_directory,
    _is_shell_sensitive_name,
)


class ToolExecutor(BaseToolExecutor):
    """Runtime ToolExecutor with false-positive-safe shell source validation."""

    def _validate_workspace_index_entry(
        self,
        directory_fd: int,
        name: str,
        metadata: os.stat_result,
        relative: str,
        is_git: bool,
    ) -> None:
        if is_git:
            if stat.S_ISLNK(metadata.st_mode):
                raise WorkspacePathError("unsupported_workspace_entry")
            if stat.S_ISREG(metadata.st_mode):
                if metadata.st_nlink != 1:
                    raise WorkspacePathError("unsupported_workspace_hardlink")
            elif not stat.S_ISDIR(metadata.st_mode):
                raise WorkspacePathError("unsupported_workspace_entry")
            if metadata.st_uid != self.workspace.owner:
                raise WorkspacePathError("unsupported_workspace_entry")
            return

        is_rust_source = (
            stat.S_ISREG(metadata.st_mode)
            and Path(name.lower()).suffix == ".rs"
        )
        if (
            relative != ".env"
            and (
                _is_shell_sensitive_name(name)
                or _is_sensitive_directory(name)
            )
            and not is_rust_source
        ):
            raise WorkspacePathError("sensitive_workspace_content")
        if stat.S_ISLNK(metadata.st_mode):
            return
        if stat.S_ISDIR(metadata.st_mode):
            return
        if not stat.S_ISREG(metadata.st_mode):
            raise WorkspacePathError("unsupported_workspace_entry")
        if metadata.st_nlink != 1:
            raise WorkspacePathError("unsupported_workspace_hardlink")
        if _contains_sensitive_pem(directory_fd, name, metadata):
            raise WorkspacePathError("sensitive_workspace_content")
