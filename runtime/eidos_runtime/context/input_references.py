"""Model-facing mentions derived from persisted input references."""
from __future__ import annotations

import re
from collections.abc import Sequence

from eidos_runtime.domain.input_reference import InputReference


_INLINE_KINDS = frozenset({"skill", "agent", "mcp", "plugin", "history"})
_INLINE_TOKEN = re.compile(r"\[([^\[\]\r\n]+)\]\(ref:([A-Za-z0-9_-]{1,64})\)")


def inline_reference_mention(reference: InputReference) -> str | None:
    if reference.kind not in _INLINE_KINDS:
        return None
    label = reference.label.replace("[", "［").replace("]", "］")
    label = re.sub(r"[\r\n]+", " ", label).strip() or reference.kind
    return ("$" if reference.kind == "skill" else "@") + label


def project_input_mentions(text: str, references: Sequence[InputReference]) -> str:
    """Replace only uniquely bound inline markers; leave literal user text intact.

    The editor accepts full IDs or prefixes of at least eight characters. Resolve
    against this message's references, never another message or a global catalog.
    Labels come from the saved reference rather than the marker's display text.
    """
    inline = {
        reference.id: reference for reference in references
        if reference.kind in _INLINE_KINDS
    }
    if not inline:
        return text

    def replace(match: re.Match[str]) -> str:
        identifier = match[2]
        matches = [
            reference for key, reference in inline.items()
            if key == identifier or (len(identifier) >= 8 and key.startswith(identifier))
        ]
        if len(matches) != 1:
            return match[0]
        return inline_reference_mention(matches[0]) or match[0]

    return _INLINE_TOKEN.sub(replace, text)
