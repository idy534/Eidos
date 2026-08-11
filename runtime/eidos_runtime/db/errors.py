from __future__ import annotations


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


class InvalidRunStateError(RuntimeError):
    pass


class ContextLimitExceeded(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class OperationConflictError(RuntimeError):
    pass


class OperationInProgressError(RuntimeError):
    pass
