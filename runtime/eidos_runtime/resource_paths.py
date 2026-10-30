"""Product-owned location shared by installers and sandbox path protection."""
from pathlib import Path


def managed_resource_directory() -> Path:
    return Path.home() / "Eidos" / "Resource"
