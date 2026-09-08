from __future__ import annotations

from enum import StrEnum

class ReconciliationDisposition(StrEnum):
    CONTINUE = "continue"
    CONTINUE_READ_ONLY = "continue_read_only"


def classify_shell_reconciliation(
    result: dict[str, object],
    *,
    manifest_before_complete: bool,
    manifest_after_complete: bool,
    refresh_error_code: str | None,
) -> ReconciliationDisposition:
    """Classify an uncertain result without terminalizing its Run.

    Workspace manifest arguments describe the evidence available to the
    caller. The canonical result field remains the authority for uncertainty.
    """
    del manifest_before_complete, manifest_after_complete, refresh_error_code
    return (
        ReconciliationDisposition.CONTINUE_READ_ONLY
        if result.get("reconciliationRequired") is True
        else ReconciliationDisposition.CONTINUE
    )
