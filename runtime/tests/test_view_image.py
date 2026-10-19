from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from pathlib import Path
import threading
import zlib

import httpx2 as httpx
import pytest
from openai import AsyncOpenAI
from pydantic_ai.messages import BinaryContent, ModelRequest, ToolReturnPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.deepseek import DeepSeekProvider

from eidos_runtime.model.client import ModelProfileSnapshot
from eidos_runtime.model.config import MODEL_CATALOG, ModelConfig, ModelProfileSpec
from eidos_runtime.model.pydantic_ai_client import PydanticAIModelClient, encode_context
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
from eidos_runtime.tools.contracts import project_tool_result
from eidos_runtime.tools.registry import ToolRegistry, VerifiedToolOutput
from eidos_runtime.domain.tool_content import ToolBinaryContent, ToolContentSnapshot
from eidos_runtime.tools.view_image import (
    MAX_VIEW_IMAGE_BYTES,
    MAX_VIEW_IMAGE_PIXELS,
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
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAMCAgMCAgMDAwMEAwMEBQgFBQQEBQoHBwYIDAoMDAsKCwsNDhIQDQ4RDgsLEBYQERMUFRUVDA8XGBYUGBIUFRT/2wBDAQMEBAUEBQkFBQkUDQsNFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBT/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGipc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD4Kooor6M+fP/Z"
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


def _execute(entry, arguments, cancel):
    raw = entry.adapter.execute(arguments, cancel)
    return raw.result if isinstance(raw, VerifiedToolOutput) else raw


def _saved_image(data: bytes) -> dict[str, object]:
    return ToolContentSnapshot(items=(ToolBinaryContent.from_bytes(data, "image/png"),)).model_dump(mode="json", by_alias=True)


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


@pytest.mark.parametrize("exists", [True, False])
def test_real_image_handler_output_satisfies_registered_result_contract(tmp_path: Path, exists: bool) -> None:
    path = tmp_path / "image.png"
    if exists:
        path.write_bytes(_png_bytes())
    entry = _entry(_authority(tmp_path))
    result = _execute(entry, {"path": path.name}, threading.Event())
    validated = entry.validate_result(result)
    assert validated["toolName"] == "view_image"
    assert validated["outcome"] == ("success" if exists else "error")


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
    assert MODEL_CATALOG.profile("minimax-m3").supports_images is True


def test_view_image_reads_a_valid_png_and_returns_canonical_metadata(tmp_path: Path) -> None:
    image = _png_bytes()
    path = tmp_path / "actual-not-png.txt"
    path.write_bytes(image)
    entry = _entry(_authority(tmp_path))

    result = _execute(entry, {"path": path.name}, threading.Event())

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

    result = _execute(entry, {"path": path.name}, threading.Event())

    assert result["outcome"] == "success"
    assert result["data"]["mime"] == "image/jpeg"


def test_view_image_rejects_invalid_image_bytes_even_with_image_extension(tmp_path: Path) -> None:
    path = tmp_path / "fake.png"
    path.write_bytes(b"not an image")
    entry = _entry(_authority(tmp_path))

    result = _execute(entry, {"path": path.name}, threading.Event())

    assert result["outcome"] == "error"
    assert result["code"] == "invalid_image"


def test_view_image_rejects_oversized_files_before_reading_them(tmp_path: Path) -> None:
    path = tmp_path / "large.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * MAX_VIEW_IMAGE_BYTES)
    entry = _entry(_authority(tmp_path))

    result = _execute(entry, {"path": path.name}, threading.Event())

    assert result["outcome"] == "error"
    assert result["code"] == "image_too_large"


def test_view_image_rejects_pixel_bomb_dimensions(tmp_path: Path) -> None:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return (
            len(payload).to_bytes(4, "big")
            + body
            + (zlib.crc32(body) & 0xFFFFFFFF).to_bytes(4, "big")
        )

    dimension = int(MAX_VIEW_IMAGE_PIXELS ** 0.5) + 1
    image = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", dimension.to_bytes(4, "big") + dimension.to_bytes(4, "big") + b"\x08\x06\x00\x00\x00")
        + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00"))
        + chunk(b"IEND", b"")
    )
    path = tmp_path / "pixel-bomb.png"
    path.write_bytes(image)

    result = _execute(_entry(_authority(tmp_path)),
        {"path": path.name}, threading.Event()
    )

    assert result["outcome"] == "error"
    assert result["code"] == "image_too_large"


def test_view_image_rejects_traversal_and_external_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(_png_bytes())
    entry = _entry(_authority(workspace))

    traversal = _execute(entry, {"path": "../outside.png"}, threading.Event())
    external = _execute(entry, {"path": str(outside)}, threading.Event())

    assert traversal["code"] == "invalid_path"
    assert external["code"] == "path_outside_authority"


def test_view_image_rejects_symlink_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(_png_bytes())
    (workspace / "link.png").symlink_to(outside)
    entry = _entry(_authority(workspace))

    result = _execute(entry, {"path": "link.png"}, threading.Event())

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

    result = _execute(entry, {"path": "assets/diagram.dat"}, threading.Event())

    assert result["outcome"] == "success"
    assert result["data"]["path"] == str(path)


def test_view_image_registry_entry_is_gated_by_model_capability(tmp_path: Path) -> None:
    authority = _authority(tmp_path)

    assert _entry(authority, supports_images=False) is None

    entry = _entry(authority)
    assert entry is not None
    assert ToolRegistry((entry,)).names == frozenset({"view_image"})
    assert entry.spec.description == (
        "View a local PNG or JPEG image when visual inspection is needed."
    )
    assert "authorized" not in entry.spec.description
    assert entry.spec.input_schema["properties"]["path"]["description"] == (
        "Path to an image in the workspace or an active Skill root."
    )


def test_view_image_projection_keeps_metadata_without_binary_data() -> None:
    data = _png_bytes()
    canonical = _canonical_view_image_result(Path("/workspace/image.png"), data)

    projection = project_tool_result("view_image", canonical)

    assert projection.model_result["data"] == {
        key: value for key, value in canonical["data"].items() if key != "sha256"
    }
    assert "sha256" not in projection.model_result["data"]
    assert "base64" not in json.dumps(projection.model_result)


def test_encode_context_projects_an_actual_binary_content_for_view_image(
    tmp_path: Path,
) -> None:
    data = _png_bytes()
    path = tmp_path / "image.png"
    path.write_bytes(data)
    canonical = _canonical_view_image_result(path, data)

    messages = encode_context(
        (
            {
                "type": "tool_call",
                "callId": "call-image",
                "name": "view_image",
                "arguments": "{}",
                "responseId": "response-1",
            },
            {
                "type": "tool_result",
                "callId": "call-image",
                "name": "view_image",
                "result": json.dumps(canonical),
                "toolContent": _saved_image(data),
            },
        ),
        supports_images=True,
    )

    assert isinstance(messages[-1], ModelRequest)
    part = messages[-1].parts[0]
    assert isinstance(part, ToolReturnPart)
    assert isinstance(part.content, list)
    assert json.loads(part.content[0])["toolName"] == "view_image"
    binary = next(value for value in part.content if isinstance(value, BinaryContent))
    assert binary.data == data
    assert binary.media_type == "image/png"


@pytest.mark.parametrize("change", ["delete", "replace"])
def test_saved_image_content_survives_source_changes(tmp_path: Path, change: str) -> None:
    path = tmp_path / "image.png"
    original = _png_bytes()
    path.write_bytes(original)
    raw = _entry(_authority(tmp_path)).adapter.execute({"path": path.name}, threading.Event())
    assert isinstance(raw, VerifiedToolOutput)
    assert raw.model_content is not None
    path.unlink()
    if change == "replace":
        path.write_bytes(_png_bytes((99, 2, 3, 255)))
    messages = encode_context((
        {"type": "tool_call", "callId": "image", "name": "view_image", "arguments": "{}", "responseId": "response"},
        {"type": "tool_result", "callId": "image", "name": "view_image", "result": json.dumps(raw.result),
         "toolContent": raw.model_content.model_dump(mode="json", by_alias=True)},
    ), supports_images=True)
    binary = next(value for value in messages[-1].parts[0].content if isinstance(value, BinaryContent))
    assert binary.data == original
    # A new read checks the live file; history does not recreate or reread it.
    current = _execute(_entry(_authority(tmp_path)), {"path": path.name}, threading.Event())
    assert current["outcome"] == ("error" if change == "delete" else "success")


def test_legacy_image_result_does_not_read_the_source(tmp_path: Path) -> None:
    canonical = _canonical_view_image_result(tmp_path / "missing.png", _png_bytes())
    messages = encode_context((
        {"type": "tool_call", "callId": "image", "name": "view_image", "arguments": "{}", "responseId": "response"},
        {"type": "tool_result", "callId": "image", "name": "view_image", "result": json.dumps(canonical)},
    ), supports_images=True)
    assert "Historical image content was not saved" in messages[-1].parts[0].content


def test_encode_context_keeps_image_result_text_only_without_image_capability(
    tmp_path: Path,
) -> None:
    data = _png_bytes()
    path = tmp_path / "image.png"
    path.write_bytes(data)
    canonical = _canonical_view_image_result(path, data)

    messages = encode_context((
        {
            "type": "tool_call",
            "callId": "call-image",
            "name": "view_image",
            "arguments": "{}",
            "responseId": "response-1",
        },
        {
            "type": "tool_result",
            "callId": "call-image",
            "name": "view_image",
            "result": json.dumps(canonical),
                "toolContent": _saved_image(data),
        },
    ))

    part = messages[-1].parts[0]
    assert isinstance(part, ToolReturnPart)
    assert isinstance(part.content, list)
    assert all(isinstance(value, str) for value in part.content)


@pytest.mark.parametrize("image_positions", [(0,), (1,), (2,), (0, 2)])
def test_chat_completion_maps_every_tool_reply_before_batch_images(
    tmp_path: Path,
    image_positions: tuple[int, ...],
) -> None:
    data = _png_bytes()
    path = tmp_path / "image.png"
    path.write_bytes(data)
    canonical = _canonical_view_image_result(path, data)
    context: list[dict[str, object]] = [{"type": "user", "content": "Inspect."}]
    for index in range(3):
        call_id = f"call-{index}"
        name = "view_image" if index in image_positions else "read_file"
        context.extend((
            {
                "type": "tool_call",
                "callId": call_id,
                "name": name,
                "arguments": "{}",
                "responseId": "response-1",
            },
            {
                "type": "tool_result",
                "callId": call_id,
                "name": name,
                "result": (
                    json.dumps(canonical)
                    if index in image_positions
                    else f"text result {index}"
                ),
                **({"toolContent": _saved_image(data)} if index in image_positions else {}),
            },
        ))

    messages = encode_context(
        tuple(context),
        supports_images=True,
    )
    tool_request = messages[-1]
    assert isinstance(tool_request, ModelRequest)
    tool_returns = [part for part in tool_request.parts if isinstance(part, ToolReturnPart)]
    assert [part.tool_call_id for part in tool_returns] == [
        "call-0", "call-1", "call-2",
    ]

    model = OpenAIChatModel(
        "deepseek-flash",
        provider=DeepSeekProvider(api_key="offline-fixture"),
    )
    wire_messages = asyncio.run(
        model._map_messages(messages, ModelRequestParameters())
    )
    assert [message["role"] for message in wire_messages] == [
        "user", "assistant", "tool", "tool", "tool", "user",
    ]
    assert [
        message["tool_call_id"]
        for message in wire_messages
        if message["role"] == "tool"
    ] == ["call-0", "call-1", "call-2"]
    image_content = wire_messages[-1]["content"]
    assert isinstance(image_content, list)
    assert sum(part["type"] == "image_url" for part in image_content) == len(
        image_positions
    )


def test_chat_completion_wire_payload_keeps_image_after_all_batch_tool_replies(
    tmp_path: Path,
) -> None:
    data = _png_bytes()
    path = tmp_path / "image.png"
    path.write_bytes(data)
    canonical = _canonical_view_image_result(path, data)
    context = (
        {"type": "user", "content": "Inspect these results."},
        {
            "type": "assistant",
            "content": "Progress.",
            "responseId": "response-1",
        },
        {
            "type": "tool_call",
            "callId": "call-image",
            "name": "view_image",
            "arguments": "{}",
            "responseId": "response-1",
        },
        {
            "type": "tool_call",
            "callId": "call-text",
            "name": "read_file",
            "arguments": "{}",
            "responseId": "response-1",
        },
        {
            "type": "tool_result",
            "callId": "call-image",
            "name": "view_image",
            "result": json.dumps(canonical),
                "toolContent": _saved_image(data),
        },
        {
            "type": "tool_result",
            "callId": "call-text",
            "name": "read_file",
            "result": "text result",
        },
    )
    path.unlink()
    payloads: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                b'data: {"id":"response-1","object":"chat.completion.chunk",'
                b'"created":0,"model":"deepseek-flash",'
                b'"choices":[{"index":0,"delta":{"content":"OK"},'
                b'"finish_reason":null}]}\n\n'
                b'data: {"id":"response-1","object":"chat.completion.chunk",'
                b'"created":0,"model":"deepseek-flash",'
                b'"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}'
                b'\n\ndata: [DONE]\n\n'
            ),
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    openai_client = AsyncOpenAI(
        api_key="offline-fixture",
        base_url="https://api.deepseek.com",
        max_retries=0,
        http_client=http_client,
    )
    kernel = RuntimeAsyncKernel()
    kernel.start()
    client = PydanticAIModelClient(
        OpenAIChatModel(
            "deepseek-flash",
            provider=DeepSeekProvider(openai_client=openai_client),
        ),
        ModelProfileSpec(
            provider_id="deepseek",
            model_id="deepseek-flash",
            context_window_tokens=4096,
            max_output_tokens=512,
            request_timeout_seconds=5.0,
            supports_images=True,
        ),
        openai_client=openai_client,
        async_kernel=kernel,
    )
    try:
        response = client.complete(
            context,
            threading.Event(),
            lambda _delta: None,
            instructions="fixture instructions",
            allow_tools=False,
        )
        assert response.text == "OK"
    finally:
        client.close()
        kernel.close()
        asyncio.run(http_client.aclose())

    assert len(payloads) == 1
    wire_messages = payloads[0]["messages"]
    assert isinstance(wire_messages, list)
    assert [message["role"] for message in wire_messages][-5:] == [
        "user", "assistant", "tool", "tool", "user",
    ]
    tool_messages = [message for message in wire_messages if message["role"] == "tool"]
    assert [message["tool_call_id"] for message in tool_messages] == [
        "call-image", "call-text",
    ]
    image_messages = [
        message
        for message in wire_messages
        if message["role"] == "user"
        and isinstance(message.get("content"), list)
        and any(part.get("type") == "image_url" for part in message["content"])
    ]
    assert len(image_messages) == 1
    last_tool_index = max(
        index for index, message in enumerate(wire_messages)
        if message["role"] == "tool"
    )
    image_index = wire_messages.index(image_messages[0])
    assert last_tool_index < image_index
