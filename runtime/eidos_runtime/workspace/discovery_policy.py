from __future__ import annotations


HARD_DISCOVERY_DIRECTORIES = frozenset({
    ".git",
    ".agents",
    ".eidos",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
})
READABLE_METADATA_DIRECTORIES = frozenset({".git", ".agents", ".eidos"})


def is_readable_metadata_path(relative_path: str) -> bool:
    parts = relative_path.split("/")
    return bool(parts) and parts[0] in READABLE_METADATA_DIRECTORIES


def is_discovery_path_allowed(
    relative_path: str, *, allow_metadata: bool = False
) -> bool:
    if (
        not relative_path
        or relative_path.startswith("/")
        or "\\" in relative_path
        or "\x00" in relative_path
    ):
        return False
    parts = relative_path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    hard_directories = (
        HARD_DISCOVERY_DIRECTORIES - READABLE_METADATA_DIRECTORIES
        if allow_metadata
        else HARD_DISCOVERY_DIRECTORIES
    )
    return not any(
        part in hard_directories
        for part in parts[:-1]
    )
