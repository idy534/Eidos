from __future__ import annotations

from typing import Literal


class StorageError(RuntimeError):
    pass


class WorkspaceBoundaryError(ValueError):
    pass


class WorkspaceIdentityChangedError(RuntimeError):
    pass


class InvalidCursorError(ValueError):
    pass


class ActiveRunError(RuntimeError):
    pass


class SessionActiveError(RuntimeError):
    pass


class ResourceNotFoundError(LookupError):
    pass


class ProjectHasSessionsError(RuntimeError):
    pass


class ProjectWorktreeRecoveryRequiredError(RuntimeError):
    pass


class InvalidRunStateError(RuntimeError):
    pass


class RunCompletionDeferred(InvalidRunStateError):
    """The final commit observed work that the sampled response did not see."""

    def __init__(self, reason: Literal[
        "pending_input", "active_children", "collaboration_changed", "cancel_requested",
    ]) -> None:
        self.reason = reason
        super().__init__(reason)


class ReconciliationRequiredError(InvalidRunStateError):
    """The Run cannot succeed until an uncertain side effect is observed."""


class ContextLimitExceeded(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class OperationConflictError(RuntimeError):
    pass


class OperationInProgressError(RuntimeError):
    pass


class RuntimeDependencySnapshotConflictError(StorageError):
    pass


class RuntimeDependencyBindingConflictError(StorageError):
    pass


class OperationFailedError(RuntimeError):
    def __init__(self, code: str, *, side_effects_may_exist: bool) -> None:
        self.code = code
        self.side_effects_may_exist = side_effects_may_exist
        super().__init__(code)
