from __future__ import annotations

import re


_PROVIDER_CONTROL_SYNTAX = re.compile(
    r"<\|{1,2}\s*DSML\s*\|{1,2}",
    re.IGNORECASE,
)


def contains_provider_control_syntax(text: str) -> bool:
    """Return whether model text contains provider tool-control markup.

    DeepSeek-compatible endpoints have emitted both ASCII ``<|DSML|...>`` and
    full-width ``<｜｜DSML｜｜...>`` envelopes. Provider control markup is protocol
    data, never assistant-visible text, so normalize the known full-width pipe
    variant before matching the envelope.
    """

    if not text:
        return False
    normalized = text.replace("｜", "|")
    return _PROVIDER_CONTROL_SYNTAX.search(normalized) is not None
