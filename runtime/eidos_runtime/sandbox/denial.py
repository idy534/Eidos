from __future__ import annotations

from enum import StrEnum

from pydantic import Field, StrictInt, StrictStr

from eidos_runtime.protocol.schemas import ClosedModel


class SandboxDenialCategory(StrEnum):
    FILESYSTEM_READ = "filesystem_read"
    FILESYSTEM_WRITE = "filesystem_write"
    NETWORK = "network"
    EXECUTION = "execution"
    PROCESS = "process"
    UNKNOWN = "unknown"


class SandboxDenied(ClosedModel):
    category: SandboxDenialCategory
    summary: StrictStr
    evidence: StrictStr | None = None
    original_exit_code: StrictInt | None = Field(
        default=None, alias="originalExitCode"
    )


def detect_sandbox_denial(
    *,
    sandboxed: bool,
    exit_code: int | None,
    stdout: str,
    stderr: str,
) -> SandboxDenied | None:
    if not sandboxed or exit_code in {None, 0}:
        return None
    evidence = f"{stderr}\n{stdout}".strip()
    lowered = evidence.lower()
    if not any(
        signature in lowered
        for signature in (
            "operation not permitted",
            "sandbox: deny",
            "network is unreachable",
        )
    ):
        return None
    if any(token in lowered for token in ("network", "connect", "socket")):
        category = SandboxDenialCategory.NETWORK
    elif any(token in lowered for token in ("write", "mkdir", "touch", "read-only")):
        category = SandboxDenialCategory.FILESYSTEM_WRITE
    elif any(token in lowered for token in ("exec", "map executable", "bad cpu")):
        category = SandboxDenialCategory.EXECUTION
    else:
        category = SandboxDenialCategory.FILESYSTEM_READ
    return SandboxDenied(
        category=category,
        summary=f"Seatbelt denied {category.value.replace('_', ' ')}",
        evidence=evidence[:2_000] or None,
        originalExitCode=exit_code,
    )
