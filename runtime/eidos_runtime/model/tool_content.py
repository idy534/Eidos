from __future__ import annotations

import base64
import json

from pydantic_ai.messages import BinaryContent

from eidos_runtime.domain.tool_content import ToolContentSnapshot, ToolTextContent
from eidos_runtime.model.client import ModelContextItem


def encode_tool_content(
    item: ModelContextItem, result: str, *, supports_images: bool,
) -> str | list[str | BinaryContent]:
    """Encode saved observations; source paths are never dereferenced here."""
    raw = item.get("toolContent")
    if raw is None:
        if item.get("toolContentOmitted"):
            return result + "\n[Saved tool content omitted from this request because of the content budget.]"
        if item.get("name") == "view_image":
            return result + "\n[Historical image content was not saved. The path is provenance only; this request contains no image.]"
        return result
    snapshot = ToolContentSnapshot.model_validate_json(json.dumps(raw))
    content: list[str | BinaryContent] = [result]
    for part in snapshot.items:
        if isinstance(part, ToolTextContent):
            content.append(part.text)
        elif supports_images and part.mime in {"image/png", "image/jpeg"}:
            content.append(BinaryContent(data=base64.b64decode(part.data), media_type=part.mime))
        else:
            # A saved binary observation is not a declaration of model capability.
            content.append(f"[Saved {part.mime} content omitted: this model has no declared support for this media type.]")
    return content


def encode_responses_tool_content(
    item: ModelContextItem, result: str, *, supports_images: bool,
) -> str | list[dict[str, str]]:
    content = encode_tool_content(item, result, supports_images=supports_images)
    if isinstance(content, str):
        return content
    return [
        {"type": "input_text", "text": part} if isinstance(part, str) else {
            "type": "input_image",
            "image_url": f"data:{part.media_type};base64,{base64.b64encode(part.data).decode('ascii')}",
        }
        for part in content
    ]
