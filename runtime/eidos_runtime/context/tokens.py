"""Offline reference token counts; provider request usage remains authoritative."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import tiktoken
from tiktoken.load import load_tiktoken_bpe


TOKEN_ESTIMATOR = "cl100k-reference-v1"
VOCABULARY_SHA256 = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
VOCABULARY_PATH = (
    Path(__file__).parents[1] / "resources/tokenizers/cl100k_base.tiktoken"
)
# The cl100k pattern is configuration from tiktoken 0.13.0 (MIT), not a tokenizer.
_PATTERN = r"'(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?+\p{L}++|\p{N}{1,3}+| ?[^\s\p{L}\p{N}]++[\r\n]*+|\s++$|\s*[\r\n]|\s+(?!\S)|\s"


@lru_cache(maxsize=1)
def _encoding() -> tiktoken.Encoding:
    # Only a fixed local path reaches the SDK loader. No model-name lookup or
    # remote vocabulary URL is used, including on a clean offline installation.
    return tiktoken.Encoding(
        name="eidos-cl100k-reference",
        pat_str=_PATTERN,
        mergeable_ranks=load_tiktoken_bpe(
            str(VOCABULARY_PATH), expected_hash=VOCABULARY_SHA256
        ),
        special_tokens={},
    )


def estimate_text_tokens(value: str) -> int:
    try:
        encoding = _encoding()
    except (OSError, ValueError):
        # A missing/corrupt resource must not make context management disappear.
        # UTF-8 bytes are an explicit conservative fallback, never an exact count.
        return max(1, len(value.encode("utf-8")))
    # Bound work for very long unbroken logs; chunk boundaries may overestimate.
    return max(
        1,
        sum(
            len(encoding.encode_ordinary(value[offset : offset + 8_192]))
            for offset in range(0, len(value), 8_192)
        ),
    )
