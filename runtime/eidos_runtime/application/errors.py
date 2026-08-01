from __future__ import annotations


class ApplicationError(RuntimeError):
    """A stable application-layer failure without JSON-RPC presentation."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


class ApplicationInvalidParamsError(ApplicationError):
    """A semantic request failure that the protocol maps to ``-32602``."""


__all__ = ["ApplicationError", "ApplicationInvalidParamsError"]
