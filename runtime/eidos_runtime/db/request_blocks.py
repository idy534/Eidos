"""Shared request blocks for Context and Step Resolution persistence."""

from __future__ import annotations

import json
from typing import Literal, cast

from pydantic import Field, JsonValue

from eidos_runtime.db.json_blobs import JsonBlobCorruptionError, JsonBlobStore
from eidos_runtime.domain.tool_content import ToolContentSnapshot
from eidos_runtime.model.client import ModelContextItem
from eidos_runtime.models import EidosFrozenStrictModel


class StoredContextItem(EidosFrozenStrictModel):
    version: Literal[1] = Field(alias="$eidosContextItem")
    item: dict[str, JsonValue]
    tool_content_ref: str | None = None
    input_image_ref: str | None = None


class StoredStepResolution(EidosFrozenStrictModel):
    version: Literal[1] = Field(alias="$eidosStepBlocks")
    snapshot: dict[str, JsonValue]
    request: dict[str, JsonValue]
    model_context_refs: tuple[str, ...] = Field(max_length=10000)
    tool_definitions_ref: str


def store_context_items(
    blobs: JsonBlobStore, context: tuple[ModelContextItem, ...],
) -> list[str]:
    references = []
    for item in context:
        payload = dict(item)
        tool_content = payload.pop("toolContent", None)
        image = payload.pop("inputImage", None)
        if tool_content is not None or image is not None:
            stored = StoredContextItem(
                version=1,
                item=cast(dict[str, JsonValue], payload),
                tool_content_ref=(
                    blobs.put_json(
                        "tool-result-content",
                        ToolContentSnapshot.model_validate_json(json.dumps(tool_content)).model_dump_json(by_alias=True),
                    ) if tool_content is not None else None
                ),
                input_image_ref=(
                    blobs.put_json("context-image", json.dumps(image))
                    if image is not None else None
                ),
            )
            encoded = json.dumps(
                stored.model_dump(mode="json", by_alias=True, exclude_none=True),
                ensure_ascii=False, sort_keys=True,
            )
        else:
            # Preserve the existing context-item digest for text-only history.
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        references.append(blobs.put_json("context-item", encoded))
    return references


def read_context_items(blobs: JsonBlobStore, references: list[str] | tuple[str, ...]) -> list[ModelContextItem]:
    items = []
    for reference in references:
        payload = json.loads(blobs.read_json(reference, expected_kind="context-item"))
        if not isinstance(payload, dict):
            raise JsonBlobCorruptionError("context item is invalid")
        if "$eidosContextItem" in payload:
            stored = StoredContextItem.model_validate_json(json.dumps(payload))
            payload = dict(stored.item)
            if stored.tool_content_ref is not None:
                payload["toolContent"] = json.loads(blobs.read_json(
                    stored.tool_content_ref, expected_kind="tool-result-content",
                ))
            if stored.input_image_ref is not None:
                payload["inputImage"] = json.loads(blobs.read_json(
                    stored.input_image_ref, expected_kind="context-image",
                ))
        items.append(payload)
    return items
