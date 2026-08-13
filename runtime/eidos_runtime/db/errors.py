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


class OperationFailedError(RuntimeError):
    def __init__(self, code: str, *, side_effects_may_exist: bool) -> None:
        self.code = code
        self.side_effects_may_exist = side_effects_may_exist
        super().__init__(code)
