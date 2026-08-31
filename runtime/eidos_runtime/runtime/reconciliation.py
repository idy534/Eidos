from __future__ import annotations

from enum import StrEnum

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
    """Keep only explicitly uncertain Shell executions behind a barrier.

    Workspace manifest completeness is observation metadata. The caller has
    already normalized observation-only failures to an explicit false value.
    A true value therefore represents an execution uncertainty and must stop
    the Run instead of entering the legacy read-only disposition.
    """
    del manifest_before_complete, manifest_after_complete, refresh_error_code
    return (
        ReconciliationDisposition.INTERRUPT
        if result.get("reconciliationRequired") is True
        else ReconciliationDisposition.CONTINUE
    )
