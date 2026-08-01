from __future__ import annotations


class ApplicationResult:
    """Marker base for typed application outcomes.

    Application results deliberately remain independent from JSON-RPC DTOs so
    the protocol layer owns response-envelope construction and validation.
    """

    __slots__ = ()


__all__ = ["ApplicationResult"]
