from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import threading
import zlib

import pytest
from pydantic_ai.messages import BinaryContent, ModelRequest, ToolReturnPart

from eidos_runtime.model.client import ModelProfileSnapshot
from eidos_runtime.model.config import MODEL_CATALOG, ModelConfig, ModelProfileSpec
from eidos_runtime.model.pydantic_ai_client import encode_context
from eidos_runtime.tools.contracts import project_tool_result
from eidos_runtime.tools.registry import ToolRegistry
from eidos_runtime.tools.view_image import (
    MAX_VIEW_IMAGE_BYTES,
    ViewImageRootAuthority,
    view_image_entry,
)


def _png_bytes(rgba: tuple[int, int, int, int] = (20, 40, 60, 255)) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return (
            len(payload).to_bytes(4, "big")
            + body
            + (zlib.crc32(body) & 0xFFFFFFFF).to_bytes(4, "big")
        )

    row = b"\x00" + bytes(rgba)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", (1).to_bytes(4, "big") + (1).to_bytes(4, "big") + b"\x08\x06\x00\x00\x00")
        + chunk(b"IDAT", zlib.compress(row))
        + chunk(b"IEND", b"")
    )


_JPEG_BYTES = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAH/AP/EABQQAQAAAAAAAAAAAAAAAAAAABD/2gAIAQEAAT8Af//Z"
)


def _authority(workspace: Path, *skill_roots: Path) -> ViewImageRootAuthority:
    return ViewImageRootAuthority(
        workspace_root=workspace,
        active_skill_roots=tuple(skill_roots),
    )


def _entry(authority: ViewImageRootAuthority, *, supports_images: bool = True):
    return view_image_entry(
        supports_images=supports_images,
        authority=authority,
    )


def _canonical_view_image_result(
    path: Path,
    data: bytes,
    *,
    mime: str = "image/png",
) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "toolContractVersion": 1,
        "toolName": "view_image",
        "outcome": "success",
        "code": "ok",
        "summary": "Image loaded",
        "data": {
            "path": str(path),
            "mime": mime,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        },
        "sideEffectsMayExist": False,
        "reconciliationRequired": False,
    }


def test_model_image_capability_flows_into_the_profile_snapshot() -> None:
    config = ModelConfig.model_validate({
        "id": "image-model",
        "name": "Image model",
        "vendor": "Fixture",
        "url": "https://fixture.invalid/chat/completions",
        "apiKey": "fixture-key",
        "supportsToolCall": True,
        "supportsImages": True,
        "supportsReasoning": False,
    })
    spec = ModelProfileSpec(
        provider_id="fixture",
        model_id="image-model",
        context_window_tokens=4_096,
        max_output_tokens=512,
        request_timeout_seconds=5.0,
        supports_images=True,
    )

    snapshot = spec.snapshot(config)

    assert snapshot.supports_images is True


def test_legacy_profile_snapshot_defaults_to_no_image_support() -> None:
    snapshot = ModelProfileSnapshot.model_validate({
        "provider_id": "fixture",
        "model_id": "old-model",
        "context_window_tokens": 4_096,
        "max_output_tokens": 512,
        "request_timeout_seconds": 5.0,
        "supports_tools": True,
        "supports_json_schema_output": False,
        "supports_reasoning": False,
    })

    assert snapshot.supports_images is False


def test_legacy_model_config_defaults_to_no_image_support() -> None:
    config = ModelConfig.model_validate({
        "id": "old-model",
        "name": "Old model",
        "vendor": "Fixture",
        "url": "https://fixture.invalid/chat/completions",
        "apiKey": "fixture-key",
        "supportsToolCall": True,
        "supportsReasoning": False,
    })

    assert config.supports_images is False
    assert MODEL_CATALOG.profile("deepseek-v4-flash").supports_images is False
    assert MODEL_CATALOG.profile("doubao-seed-evolving").supports_images is True


def test_view_image_reads_a_valid_png_and_returns_canonical_metadata(tmp_path: Path) -> None:
    image = _png_bytes()
    path = tmp_path / "actual-not-png.txt"
    path.write_bytes(image)
    entry = _entry(_authority(tmp_path))

    result = entry.adapter.execute({"path": path.name}, threading.Event())

    assert result["outcome"] == "success"
    assert result["data"] == {
        "path": str(path),
        "mime": "image/png",
        "size": len(image),
        "sha256": hashlib.sha256(image).hexdigest(),
    }


def test_view_image_reads_a_valid_jpeg_using_magic_and_structure(tmp_path: Path) -> None:
    path = tmp_path / "image.with-wrong-extension.bin"
    path.write_bytes(_JPEG_BYTES)
    entry = _entry(_authority(tmp_path))

    result = entry.adapter.execute({"path": path.name}, threading.Event())

    assert result["outcome"] == "success"
    assert result["data"]["mime"] == "image/jpeg"


def test_view_image_rejects_invalid_image_bytes_even_with_image_extension(tmp_path: Path) -> None:
    path = tmp_path / "fake.png"
    path.write_bytes(b"not an image")
    entry = _entry(_authority(tmp_path))

    result = entry.adapter.execute({"path": path.name}, threading.Event())

    assert result["outcome"] == "error"
    assert result["code"] == "invalid_image"


def test_view_image_rejects_oversized_files_before_reading_them(tmp_path: Path) -> None:
    path = tmp_path / "large.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * MAX_VIEW_IMAGE_BYTES)
    entry = _entry(_authority(tmp_path))

    result = entry.adapter.execute({"path": path.name}, threading.Event())

    assert result["outcome"] == "error"
    assert result["code"] == "image_too_large"


def test_view_image_rejects_traversal_and_external_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(_png_bytes())
    entry = _entry(_authority(workspace))

    traversal = entry.adapter.execute({"path": "../outside.png"}, threading.Event())
    external = entry.adapter.execute({"path": str(outside)}, threading.Event())

    assert traversal["code"] == "invalid_path"
    assert external["code"] == "path_outside_authority"


def test_view_image_rejects_symlink_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(_png_bytes())
    (workspace / "link.png").symlink_to(outside)
    entry = _entry(_authority(workspace))

    result = entry.adapter.execute({"path": "link.png"}, threading.Event())

    assert result["outcome"] == "error"
    assert result["code"] == "unsafe_path"


def test_view_image_accepts_an_authorized_active_skill_root(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    skill_root = tmp_path / "skill"
    workspace.mkdir()
    skill_root.mkdir()
    path = skill_root / "assets" / "diagram.dat"
    path.parent.mkdir()
    path.write_bytes(_png_bytes((1, 2, 3, 255)))
    entry = _entry(_authority(workspace, skill_root))

    result = entry.adapter.execute({"path": "assets/diagram.dat"}, threading.Event())

    assert result["outcome"] == "success"
    assert result["data"]["path"] == str(path)


def test_view_image_registry_entry_is_gated_by_model_capability(tmp_path: Path) -> None:
    authority = _authority(tmp_path)

    assert _entry(authority, supports_images=False) is None

    entry = _entry(authority)
    assert entry is not None
    assert ToolRegistry((entry,)).names == frozenset({"view_image"})


def test_view_image_projection_keeps_metadata_without_binary_data() -> None:
    data = _png_bytes()
    canonical = _canonical_view_image_result(Path("/workspace/image.png"), data)

    projection = project_tool_result("view_image", canonical)

    assert projection.model_result["data"] == canonical["data"]
    assert "base64" not in json.dumps(projection.model_result)


def test_encode_context_projects_an_actual_binary_content_for_view_image(
    tmp_path: Path,
) -> None:
    data = _png_bytes()
    path = tmp_path / "image.png"
    path.write_bytes(data)
    canonical = _canonical_view_image_result(path, data)

    messages = encode_context(
        ({
            "type": "tool_result",
            "callId": "call-image",
            "name": "view_image",
            "result": json.dumps(canonical),
        },),
        supports_images=True,
        image_authority=_authority(tmp_path),
    )

    assert isinstance(messages[0], ModelRequest)
    part = messages[0].parts[0]
    assert isinstance(part, ToolReturnPart)
    assert isinstance(part.content, list)
    assert any(isinstance(value, str) and '"toolName":"view_image"' in value for value in part.content)
    binary = next(value for value in part.content if isinstance(value, BinaryContent))
    assert binary.data == data
    assert binary.media_type == "image/png"


def test_encode_context_fails_closed_when_view_image_hash_changes(tmp_path: Path) -> None:
    original = _png_bytes()
    path = tmp_path / "image.png"
    path.write_bytes(original)
    canonical = _canonical_view_image_result(path, original)
    path.write_bytes(_png_bytes((99, 2, 3, 255)))

    with pytest.raises(ValueError, match="view_image_result_changed"):
        encode_context(
            ({
                "type": "tool_result",
                "callId": "call-image",
                "name": "view_image",
                "result": json.dumps(canonical),
            },),
            supports_images=True,
            image_authority=_authority(tmp_path),
        )


def test_encode_context_keeps_image_result_text_only_without_image_capability(
    tmp_path: Path,
) -> None:
    data = _png_bytes()
    path = tmp_path / "image.png"
    path.write_bytes(data)
    canonical = _canonical_view_image_result(path, data)

    messages = encode_context(({
        "type": "tool_result",
        "callId": "call-image",
        "name": "view_image",
        "result": json.dumps(canonical),
    },), image_authority=_authority(tmp_path))

    part = messages[0].parts[0]
    assert isinstance(part, ToolReturnPart)
    assert isinstance(part.content, str)
