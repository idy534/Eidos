from __future__ import annotations


class RepositoryError(RuntimeError):
    """Base error for typed persistence interfaces."""


class PersistenceCorruptionError(RepositoryError):
    """A persisted record cannot be decoded without coercion."""

    def __init__(self, code: str, *, record: str, field: str | None = None) -> None:
        self.code = code
        self.record = record
        self.field = field
        super().__init__(code)


class ConditionalUpdateFailed(RepositoryError):
    """A compare-and-set or conditional update did not change one record."""
