"""Source-labelled context records, separate from human messages and policy."""
from __future__ import annotations

from html import escape
import json
from typing import Literal

from eidos_runtime.models import EidosFrozenStrictModel
from eidos_runtime.model.client import ModelContextItem


class SourceContext(EidosFrozenStrictModel):
    section: str
    source: str
    purpose: Literal["scoped_guidance", "evidence"] = "evidence"
    content: str

    def render(self) -> str:
        # JSON escaping keeps source text from closing the Runtime's boundary.
        body = json.dumps(self.content, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")
        return (
            f'<eidos_context section="{escape(self.section, quote=True)}" '
            f'source="{escape(self.source, quote=True)}" purpose="{self.purpose}">\n'
            + body + "\n</eidos_context>"
        )


def render_source_context(item: ModelContextItem) -> str:
    section = str(item.get("sectionId") or "historical-evidence")
    guidance = section.startswith(("project-rule:", "selected-skill:", "personalization-", "custom-agent"))
    return SourceContext(
        section=section,
        source=str(item.get("source") or section),
        purpose="scoped_guidance" if guidance else "evidence",
        content=str(item.get("content", "")),
    ).render()


def is_source_context(item: ModelContextItem) -> bool:
    """Old snapshots keep their stored shape; adapters retain their provenance."""
    return item.get("type") == "context" or (
        item.get("type", item.get("role")) == "user"
        and isinstance(item.get("sectionId"), str)
        and not str(item["sectionId"]).startswith("input-reference:")
    )
