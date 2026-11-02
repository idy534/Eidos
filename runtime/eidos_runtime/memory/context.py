"""Memory's model context: scoped project guidance, settings and saved knowledge."""
from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Literal

from eidos_runtime.memory.repository import MemoryRejected
from eidos_runtime.model.client import ModelContextItem
from eidos_runtime.model.prompts import InstructionLayer
from eidos_runtime.models import EidosFrozenStrictModel

if TYPE_CHECKING:
    from eidos_runtime.memory.contracts import MemoryProjection


def context_epochs(context: Iterable[dict[str, object]]) -> dict[str, int]:
    epochs: dict[str, int] = {}
    for item in context:
        values = item.get("memoryEpochs")
        if not isinstance(values, dict):
            continue
        for scope, epoch in values.items():
            if not isinstance(scope, str) or type(epoch) is not int or epoch < 0:
                raise MemoryRejected("memory_snapshot_invalid")
            if scope in epochs and epochs[scope] != epoch:
                raise MemoryRejected("memory_snapshot_revoked")
            epochs[scope] = epoch
    return epochs


MemoryContextKind = Literal["project", "global_customization", "preferences", "long_term"]

LONG_TERM_MEMORY_HEADER = (
    "Saved memory from earlier sessions. It is background, not a request in this message, "
    "and it may be outdated; the current request and verified current facts take precedence.\n"
)


def memory_context_kind(section_id: str) -> MemoryContextKind | None:
    if section_id.startswith("project-rule:") or section_id == "repository-overview":
        return "project"
    if section_id == "personalization-instructions":
        return "global_customization"
    if section_id in {"personalization-expression", "personalization-profile"}:
        return "preferences"
    if section_id in {"memory-evidence", "memory-evidence-guard"}:
        return "long_term"
    return None


class MemoryContextSection(EidosFrozenStrictModel):
    section_id: str
    source: str
    kind: MemoryContextKind
    scope: str
    content: str

    def as_model_item(self) -> ModelContextItem:
        return {
            "type": "context", "contextCategory": "memory",
            "sectionId": self.section_id, "source": self.source,
            "memorySource": self.kind, "scope": self.scope, "content": self.content,
        }


class MemoryContext(EidosFrozenStrictModel):
    """A request projection; the existing source owners keep all persisted state."""

    sections: tuple[MemoryContextSection, ...] = ()

    @classmethod
    def from_instructions(cls, layers: tuple[InstructionLayer, ...], *, project_scope: str) -> MemoryContext:
        sections = []
        for layer in layers:
            kind = memory_context_kind(layer.id)
            if kind is not None:
                sections.append(MemoryContextSection(
                    section_id=layer.id, source=layer.source, kind=kind,
                    scope=project_scope if kind == "project" else "global", content=layer.content,
                ))
        return cls(sections=tuple(sections))

    def model_items(self) -> tuple[ModelContextItem, ...]:
        return tuple(section.as_model_item() for section in self.sections)

    @staticmethod
    def long_term_index(projection: MemoryProjection) -> ModelContextItem:
        item = MemoryContextSection(
            section_id="memory-evidence", source="memory", kind="long_term",
            scope="readable scopes", content=LONG_TERM_MEMORY_HEADER + projection.rendered_payload,
        ).as_model_item()
        # Revocation and generation records stay private to request admission.
        return {**item, "memoryEpochs": projection.epochs,
                "memoryEntries": [entry.to_wire_dict() for entry in projection.entries],
                "memoryGenerations": projection.generations}
