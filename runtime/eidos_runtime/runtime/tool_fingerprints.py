from __future__ import annotations

import hashlib

from eidos_runtime.model.client import (
    CustomToolPayload,
    FunctionToolPayload,
    ToolPayload,
)


def tool_payload_fingerprint_value(payload: ToolPayload) -> dict[str, object]:
    if isinstance(payload, FunctionToolPayload):
        return {"kind": "function", "arguments": payload.arguments}
    if isinstance(payload, CustomToolPayload):
        encoded = payload.input.encode("utf-8")
        return {
            "kind": "custom",
            "inputSha256": hashlib.sha256(encoded).hexdigest(),
            "inputBytes": len(encoded),
        }
    raise ValueError("unsupported_tool_payload")


__all__ = ["tool_payload_fingerprint_value"]
