from __future__ import annotations

from enum import StrEnum

from eidos_runtime.sandbox.permissions import SandboxPermissions


class ReconciliationDisposition(StrEnum):
    CONTINUE = "continue"
    CONTINUE_READ_ONLY = "continue_read_only"
    INTERRUPT = "interrupt"


def classify_shell_reconciliation(
    result: dict[str, object],
    *,
    manifest_before_complete: bool,
    manifest_after_complete: bool,
    refresh_error_code: str | None,
) -> ReconciliationDisposition:
    """Choose whether a completed Shell attempt can stay in the current Run."""
    if result.get("reconciliationRequired") is not True:
        return ReconciliationDisposition.CONTINUE
    if not _is_known_default_sandbox_exit(result):
        return ReconciliationDisposition.INTERRUPT
    if refresh_error_code == "WORKSPACE_INDEX_INCOMPLETE":
        return ReconciliationDisposition.CONTINUE_READ_ONLY
    if refresh_error_code is not None:
        return ReconciliationDisposition.INTERRUPT
    if not manifest_before_complete and manifest_after_complete:
        return ReconciliationDisposition.CONTINUE_READ_ONLY
    return ReconciliationDisposition.INTERRUPT


def _is_known_default_sandbox_exit(result: dict[str, object]) -> bool:
    if result.get("outcome") != "error" or result.get("code") != "nonzero_exit":
        return False
    data = result.get("data")
    if not isinstance(data, dict):
        return False
    exit_code = data.get("exitCode")
    if (
        not isinstance(exit_code, int)
        or isinstance(exit_code, bool)
        or exit_code == 0
    ):
        return False
    if (
        data.get("termination") != "exit"
        or data.get("sandboxed") is not True
        or data.get("escalated") is not False
        or data.get("sandboxPermissions") != SandboxPermissions.USE_DEFAULT.value
        or data.get("sandboxDenialCategory") is not None
    ):
        return False
    permissions = data.get("effectivePermissionsSummary")
    if not isinstance(permissions, dict):
        return False
    read = permissions.get("read")
    write = permissions.get("write")
    execute = permissions.get("execute")
    return (
        permissions.get("networkEnabled") is False
        and isinstance(read, list)
        and read == []
        and isinstance(write, list)
        and write == []
        and isinstance(execute, list)
        and execute == []
    )
