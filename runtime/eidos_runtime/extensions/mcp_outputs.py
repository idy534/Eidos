from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from uuid import UUID

from eidos_runtime.db.database import WorkspaceIdentity
from eidos_runtime.extensions.contracts import McpServerConfigV1
from eidos_runtime.workspace.projectless import ProjectlessDirectoryAllocator
from eidos_runtime.workspace.reader import capture_workspace_identity


_SOURCE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_PLAYWRIGHT_PACKAGE = re.compile(r"^@playwright/mcp(?:@[^/\\\s]+)?$")
_PLAYWRIGHT_FILE_OUTPUTS = frozenset({
    "browser_take_screenshot", "browser_snapshot", "browser_pdf_save",
    "browser_console_messages", "browser_network_requests", "browser_network_request",
    "browser_storage_state", "browser_start_video", "browser_stop_video",
})


@dataclass(frozen=True)
class McpOutputDirectory:
    identity: WorkspaceIdentity

    @property
    def path(self) -> Path:
        return self.identity.path

    @classmethod
    def create(cls, plugin_id: str, server_id: str, run_id: str) -> McpOutputDirectory:
        if not _SOURCE_ID.fullmatch(plugin_id) or not _SOURCE_ID.fullmatch(server_id):
            raise ValueError("invalid_mcp_output_source")
        run = str(UUID(run_id))
        configured = os.environ.get("EIDOS_MCP_OUTPUT_ROOT")
        root = Path(configured) if configured else Path.home().resolve() / "Eidos" / "eidos-mcp"
        if not root.is_absolute() or ".." in root.parts:
            raise ValueError("invalid_mcp_output_root")
        # Dots cannot occur in either ID, so the source folder is unambiguous.
        path = root / f"{plugin_id}.{server_id}" / run
        return cls._create_or_open(path)

    @classmethod
    def _create_or_open(cls, path: Path) -> McpOutputDirectory:
        allocator = ProjectlessDirectoryAllocator()
        try:
            identity = allocator.create(path)
        except FileExistsError:
            identity = capture_workspace_identity(path)
            if identity.path != path:
                raise ValueError("mcp_output_directory_changed")
            allocator.verify(identity)
        return cls(identity)

    def verify(self) -> None:
        ProjectlessDirectoryAllocator().verify(self.identity)

    def home_path(self) -> Path:
        self.verify()
        # Configuration, credentials and caches outlive a Run.
        directory = self._create_or_open(self.path.parent / "h")
        self.verify()
        return directory.path

    def playwright_arguments(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        filename = arguments.get("filename")
        if name not in _PLAYWRIGHT_FILE_OUTPUTS or not isinstance(filename, str) or not filename:
            return arguments
        candidate = Path(filename)
        if candidate.is_absolute():
            return arguments
        if ".." in candidate.parts or "\\" in filename or any(
            ord(character) < 32 or ord(character) == 127 for character in filename
        ):
            raise ValueError("invalid_mcp_output_filename")
        return {**arguments, "filename": str(self.path / candidate)}


def is_playwright_server(config: McpServerConfigV1) -> bool:
    executable = Path(config.executable).name
    if executable == "playwright-mcp":
        return True
    if executable == "npx":
        arguments = [value for value in config.argv if value not in {"-y", "--yes"}]
        return bool(arguments and _PLAYWRIGHT_PACKAGE.fullmatch(arguments[0]))
    return executable == "node" and bool(config.argv) and config.argv[0].endswith(
        "/node_modules/@playwright/mcp/cli.js"
    )
