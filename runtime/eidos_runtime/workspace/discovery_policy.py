from __future__ import annotations

from pathlib import Path


HARD_DISCOVERY_DIRECTORIES = frozenset({
    ".git",
    ".eidos",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
})
SENSITIVE_DIRECTORIES = frozenset({
    ".aws", ".config", ".eidos", ".gnupg", ".kube", ".ssh"
})
SENSITIVE_NAMES = frozenset({
    ".git-credentials",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "credentials",
    "credentials.json",
    "id_dsa",
    "id_ed25519",
    "id_rsa",
})
SENSITIVE_SUFFIXES = frozenset({".key", ".p12", ".pem", ".pfx"})
SENSITIVE_KEYWORDS = frozenset({"credential", "secret", "token"})


def is_sensitive_directory(name: str) -> bool:
    return name.lower() in SENSITIVE_DIRECTORIES


def is_sensitive_name(name: str) -> bool:
    lowered = name.lower()
    if lowered == ".env.example":
        return False
    return (
        lowered.startswith(".eidos-")
        or lowered == ".env"
        or lowered.startswith(".env.")
        or lowered in SENSITIVE_NAMES
        or Path(lowered).suffix in SENSITIVE_SUFFIXES
        or any(keyword in lowered for keyword in SENSITIVE_KEYWORDS)
    )


def is_discovery_path_allowed(relative_path: str) -> bool:
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
    if any(is_sensitive_name(part) for part in parts):
        return False
    return not any(
        part in HARD_DISCOVERY_DIRECTORIES or is_sensitive_directory(part)
        for part in parts[:-1]
    )
