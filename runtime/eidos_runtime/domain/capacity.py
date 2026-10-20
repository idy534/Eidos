"""Eidos capacity policy: request limits trigger smaller requests instead of stopping a Run."""
from __future__ import annotations


class CapacityExceeded(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def model_output_byte_budget(max_output_tokens: int) -> int:
    # A defensive memory allowance, not a tokenizer or a Provider capability.
    # Oversized text becomes a preview; the Run continues in smaller sections.
    return max(512 * 1024, max_output_tokens * 128)
