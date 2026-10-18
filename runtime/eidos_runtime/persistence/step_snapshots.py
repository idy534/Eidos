from __future__ import annotations

import json

from eidos_runtime.db.json_blobs import JsonBlobReference, JsonBlobStore
from eidos_runtime.db.request_blocks import (
    StoredStepResolution, read_context_items, store_context_items,
)
from eidos_runtime.runtime.resolution import StepResolutionSnapshot, canonical_json


def store_step_snapshot(blobs: JsonBlobStore, snapshot: StepResolutionSnapshot) -> str:
    payload = snapshot.model_dump(mode="json")
    context = json.loads(payload.pop("context_payload_json"))
    request = json.loads(payload.pop("final_request_json"))
    request.pop("messages")
    tools = request.pop("tools")
    manifest = StoredStepResolution(
        version=1,
        snapshot=payload,
        request=request,
        model_context_refs=tuple(store_context_items(blobs, tuple(context))),
        tool_definitions_ref=blobs.put_json(
            "context-tools", json.dumps(tools, ensure_ascii=False, sort_keys=True),
        ),
    )
    return blobs.put_json("step-resolution-v2", manifest.model_dump_json(by_alias=True))


def read_step_snapshot(blobs: JsonBlobStore, stored: str) -> StepResolutionSnapshot:
    reference = JsonBlobReference.from_json(stored)
    if reference is None or reference.kind == "step-resolution":
        return StepResolutionSnapshot.model_validate_json(
            blobs.read_json(stored, expected_kind="step-resolution"),
        )
    manifest = StoredStepResolution.model_validate_json(
        blobs.read_json(stored, expected_kind="step-resolution-v2"),
    )
    context = read_context_items(blobs, manifest.model_context_refs)
    request = {
        **manifest.request,
        "messages": context,
        "tools": json.loads(blobs.read_json(
            manifest.tool_definitions_ref, expected_kind="context-tools",
        )),
    }
    return StepResolutionSnapshot.model_validate_json(canonical_json({
        **manifest.snapshot,
        "context_payload_json": canonical_json(context),
        "final_request_json": canonical_json(request),
    }))
