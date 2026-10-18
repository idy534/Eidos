from __future__ import annotations

import json
from pathlib import Path
import threading
from unittest.mock import patch

import pytest

from eidos_runtime.context.budget import estimate_model_request_budget
from eidos_runtime.context.plan import ContextPlanner
from eidos_runtime.db.json_blobs import JsonBlobCorruptionError, JsonBlobReference, JsonBlobStore
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.db.request_blocks import read_context_items, store_context_items
from eidos_runtime.domain.tool_content import ToolBinaryContent, ToolContentSnapshot, ToolTextContent
from eidos_runtime.model.client import ModelResponse, ScriptedModel
from eidos_runtime.model.instructions import InstructionResolver
from eidos_runtime.persistence.step_snapshots import read_step_snapshot, store_step_snapshot
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.runtime.resolution import (
    RuleResolutionSnapshot, canonical_json, create_step_resolution_snapshot,
)


def test_large_step_and_context_share_saved_media_and_survive_restart_and_gc(tmp_path: Path) -> None:
    data = tmp_path / "data"
    root = tmp_path / "workspace"
    root.mkdir()
    store = SessionStore(data)
    store.initialize()
    try:
        session = store.create_session(str(root))
        run, _ = store.create_run(session["id"], "inspect")
        rules = RuleResolutionSnapshot.create(
            workspace_root=str(root), cwd=str(root), budget_bytes=0, used_bytes=0,
            rules=(), shadowed=(), warnings=(),
        )
        instructions = InstructionResolver().resolve(rule_snapshot=rules)
        media = ToolContentSnapshot(items=(ToolBinaryContent.from_bytes(b"x" * (3 * 1024 * 1024), "image/png"),))
        blobs = store.database.json_blobs
        media_ref = blobs.put_json("tool-result-content", media.model_dump_json(by_alias=True))
        context = tuple({
            "type": "tool_result", "callId": f"image-{index}", "name": "view_image",
            "result": "{}", "toolContent": media.model_dump(mode="json", by_alias=True),
        } for index in range(4))
        snapshot = create_step_resolution_snapshot(
            run_snapshot=store.read_run_resolution_snapshot(run["id"]),
            rule_snapshot=rules, tool_snapshot={"toolSetHash": "a" * 64},
            model_context=context, tool_definitions=(), instructions=instructions,
            workspace_version=0, effective_cwd=str(root), created_at=1,
        )
        assert len(canonical_json(snapshot.model_dump(mode="json")).encode()) > 32 * 1024 * 1024
        store.increment_model_step(
            run["id"], tool_snapshot={"toolSetHash": "a" * 64},
            rule_resolution_snapshot=rules, resolution_snapshot=snapshot,
        )
        budget = estimate_model_request_budget(
            context, instructions=instructions.system_text, tool_definitions=(),
            context_window_tokens=1048576, request_max_output_tokens=4096,
        )
        plan = ContextPlanner().capture(
            model_profile=store.read_model_profile(run["id"]), rule_snapshot=rules,
            model_context=context, instructions=instructions.system_text,
            tool_definitions=(), token_budget=budget,
        )
        attempt = store.read_current_step_fact(run["id"])["modelAttemptId"]
        model_snapshot = plan.for_model_attempt(
            attempt, model_context=context, instructions=instructions.system_text, tool_definitions=(),
        )
        store.context_snapshot_repository().persist(run_id=run["id"], retrieval=None, snapshot=model_snapshot)
        step_stored = store.connection.execute("SELECT snapshot_json FROM step_resolution_snapshots WHERE id=?", (snapshot.id,)).fetchone()[0]
        step_manifest = json.loads(blobs.read_json(step_stored, expected_kind="step-resolution-v2"))
        context_stored = store.connection.execute("SELECT snapshot_json FROM context_snapshots WHERE id=?", (model_snapshot.snapshot_id,)).fetchone()[0]
        context_manifest = json.loads(blobs.read_json(context_stored, expected_kind="context-snapshot-v2"))
        assert step_manifest["modelContextRefs"] == context_manifest["modelContextRefs"]
        assert len(json.dumps(step_manifest)) < 16000
        assert len(list(blobs.root.joinpath("tool-result-content").rglob("*.json.gz"))) == 1
        for ref in step_manifest["modelContextRefs"]:
            item = json.loads(blobs.read_json(ref, expected_kind="context-item"))
            assert item["toolContentRef"] == media_ref
            assert "toolContent" not in item["item"]
        # Either snapshot alone must retain media even without a ToolCall root.
        for root_ref in (step_stored, context_stored):
            blobs.garbage_collect([root_ref])
            if root_ref == step_stored:
                assert read_step_snapshot(blobs, step_stored) == snapshot
            else:
                assert store.context_snapshot_repository().read(model_snapshot.snapshot_id) == model_snapshot
            # Restore the other root for the second GC case.
            step_stored = store_step_snapshot(blobs, snapshot)
            store.context_snapshot_repository()._store_snapshot(model_snapshot)
        store.collect_unreferenced_blobs()
    finally:
        store.close()
    reopened = SessionStore(data)
    reopened.initialize()
    try:
        assert reopened.health_state == "ready"
        reopened.collect_unreferenced_blobs()
        assert reopened.read_step_resolution_snapshots(run["id"]) == (snapshot,)
        assert reopened.context_snapshot_repository().read(model_snapshot.snapshot_id) == model_snapshot
        # Existing inline and legacy Blob representations remain readable.
        legacy = canonical_json(snapshot.model_dump(mode="json"))
        assert read_step_snapshot(reopened.database.json_blobs, legacy) == snapshot
        legacy_ref = reopened.database.json_blobs.put_json("step-resolution", legacy)
        assert read_step_snapshot(reopened.database.json_blobs, legacy_ref) == snapshot
    finally:
        reopened.close()


def test_snapshot_blob_over_32_mib_round_trips_without_recompressing_duplicates(tmp_path: Path) -> None:
    blobs = JsonBlobStore(tmp_path / "data")
    value = json.dumps({"text": "x" * (33 * 1024 * 1024)})
    stored = blobs.put_json("context-item", value)
    assert JsonBlobReference.from_json(stored).uncompressed_bytes > 32 * 1024 * 1024
    assert blobs.read_json(stored, expected_kind="context-item") == value
    with patch("eidos_runtime.db.json_blobs.gzip.compress", side_effect=AssertionError("duplicate compression")):
        assert blobs.put_json("context-item", value) == stored
    ref = json.loads(stored)
    ref["$eidosBlob"]["uncompressedBytes"] += 1
    with pytest.raises(JsonBlobCorruptionError, match="checksum"):
        blobs.read_json(json.dumps(ref), expected_kind="context-item")


def test_shared_blocks_preserve_file_content_input_images_and_legacy_items(tmp_path: Path) -> None:
    blobs = JsonBlobStore(tmp_path / "data")
    report = ToolContentSnapshot(items=(ToolTextContent(text="Captured file contents\n界"),))
    tool_ref = blobs.put_json("tool-result-content", report.model_dump_json(by_alias=True))
    context = (
        {"type": "tool_result", "callId": "file", "result": "{}", "toolContent": report.model_dump(mode="json", by_alias=True)},
        {"type": "user", "inputImage": "cG5n", "mime": "image/png", "content": "look"},
    )
    references = store_context_items(blobs, context)
    old_ref = blobs.put_json("context-item", json.dumps({"type": "user", "content": "old item"}))
    blobs.garbage_collect([*references, old_ref])
    assert read_context_items(blobs, references) == list(context)
    assert read_context_items(blobs, [old_ref]) == [{"type": "user", "content": "old item"}]
    assert json.loads(blobs.read_json(references[0], expected_kind="context-item"))["toolContentRef"] == tool_ref
    assert store_context_items(blobs, tuple(dict(reversed(list(item.items()))) for item in context)) == references


@pytest.mark.parametrize("damage", ["wrong_kind", "missing", "corrupt"])
def test_invalid_shared_block_prevents_gc_or_snapshot_acceptance(tmp_path: Path, damage: str) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        session = store.create_session(str(root))
        run, _ = store.create_run(session["id"], "inspect")
        RuntimeEngine(store, ScriptedModel([ModelResponse(text="done")]), lambda _: None).run(run["id"], threading.Event())
        snapshot = store.read_step_resolution_snapshots(run["id"])[0]
        stored = store_step_snapshot(store.database.json_blobs, snapshot)
        blobs = store.database.json_blobs
        manifest = json.loads(blobs.read_json(stored, expected_kind="step-resolution-v2"))
        block = manifest["modelContextRefs"][0]
        orphan = blobs.put_json("orphan", '{"keep_until_scan_succeeds":true}')
        if damage == "wrong_kind":
            manifest["modelContextRefs"][0] = orphan
            stored = blobs.put_json("step-resolution-v2", json.dumps(manifest))
        else:
            path = blobs.root / JsonBlobReference.from_json(block).relative_path
            if damage == "missing":
                path.unlink()
            else:
                path.write_bytes(b"bad")
        with pytest.raises(JsonBlobCorruptionError):
            blobs.garbage_collect([stored])
        assert blobs.read_json(orphan, expected_kind="orphan")
        with pytest.raises((JsonBlobCorruptionError, ValueError)):
            read_step_snapshot(blobs, stored)
    finally:
        store.close()
