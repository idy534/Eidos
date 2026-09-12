from __future__ import annotations

import json
from pathlib import Path
import threading

import pytest
import httpx
from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from eidos_runtime.model.config import ModelProfileSpec
from eidos_runtime.model.pydantic_ai_client import PydanticAIModelClient, encode_tool_definition
from eidos_runtime.model_gateway.native_custom import encode_responses_tool_definition as encode_native_definition
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
from eidos_runtime.tools.workspace import ToolExecutor


PATCH = (
    "*** Begin Patch\n"
    "*** Update File: source.py\n"
    "*** Move to: nested/target.py\n"
    "@@\n"
    " def greet():\n"
    '-    return "old"\n'
    '+    return "你好 \\\\"quoted\\\\""\n'
    "*** Add File: empty.txt\n"
    "*** Delete File: obsolete.txt\n"
    "*** End Patch\n"
)


@pytest.mark.parametrize("custom", [False, True])
def test_transports_preserve_the_same_patch_and_verified_files(
    tmp_path: Path, custom: bool,
) -> None:
    (tmp_path / "source.py").write_text('def greet():\n    return "old"\n')
    (tmp_path / "obsolete.txt").write_text("remove\n")
    with ToolExecutor(
        tmp_path, supports_custom_tools=custom, supports_tool_grammar=custom,
    ) as executor:
        entry = executor.registry.get("apply_patch")
        assert entry is not None
        if custom:
            validation = entry.validate_custom_input(PATCH)
            arguments = validation.normalized_input
        else:
            definition = next(
                tool for tool in executor.registry.model_definitions()
                if tool.name == "apply_patch"
            )
            schema = encode_tool_definition(definition).parameters_json_schema
            assert encode_native_definition(definition)["parameters"] == schema
            assert set(schema["properties"]) == {"patch"}
            assert schema["required"] == ["patch"]
            assert schema["additionalProperties"] is False
            payload = json.loads(json.dumps({"patch": PATCH}, ensure_ascii=False))
            validation = entry.validate_arguments(payload)
            arguments = validation.normalized_arguments
            assert arguments == payload
        assert validation.valid
        prepared = executor.prepare_file_change("apply_patch", arguments, threading.Event())
        assert not isinstance(prepared, dict), prepared
        result, _ = executor.commit_patch("apply_patch", prepared, threading.Event())
    assert result["outcome"] == "success"
    assert (tmp_path / "nested/target.py").read_text() == (
        'def greet():\n    return "你好 \\\\"quoted\\\\""\n'
    )
    assert (tmp_path / "empty.txt").read_bytes() == b""
    assert not (tmp_path / "source.py").exists()
    assert not (tmp_path / "obsolete.txt").exists()


@pytest.mark.parametrize("arguments", [
    {"changes": [{"type": "update", "path": "source.py", "chunks": {
        "item": {"oldLines": "old", "newLines": "new", "endOfFile": "false"},
    }}]},
    {"patch": PATCH, "changes": []},
    {"patch": False},
    {"patch": {"item": PATCH}},
])
def test_invalid_inputs_are_not_guessed_or_written(tmp_path: Path, arguments: object) -> None:
    target = tmp_path / "source.py"
    target.write_text("unchanged\n")
    with ToolExecutor(tmp_path) as executor:
        result = executor.prepare_file_change("apply_patch", arguments, threading.Event())
    assert isinstance(result, dict)
    assert result["code"] == "invalid_arguments"
    assert target.read_text() == "unchanged\n"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["source.py"]


def test_sdk_wire_schema_and_streamed_patch_round_trip(tmp_path: Path) -> None:
    """Inspect the final SDK request, not only Eidos's pre-adapter definition."""
    payloads = []
    arguments = json.dumps({"patch": PATCH}, ensure_ascii=False)

    async def respond(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        deltas = [
            {"tool_calls": [{"index": 0, "id": "patch-1", "type": "function", "function": {
                "name": "apply_patch", "arguments": arguments[:37],
            }}]},
            {"tool_calls": [{"index": 0, "function": {"arguments": arguments[37:]}}]},
        ]
        events = []
        for index, delta in enumerate([*deltas, {}]):
            events.append("data: " + json.dumps({
                "id": "completion-1", "object": "chat.completion.chunk",
                "created": 0, "model": "minimax-m3",
                "choices": [{"index": 0, "delta": delta,
                             "finish_reason": "tool_calls" if index == 2 else None}],
            }))
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"},
            content="\n\n".join([*events, "data: [DONE]", ""]),
        )

    with ToolExecutor(tmp_path) as executor:
        definition = next(
            tool for tool in executor.registry.model_definitions() if tool.name == "apply_patch"
        )
        kernel = RuntimeAsyncKernel()
        kernel.start()
        openai_client = AsyncOpenAI(
            api_key="test-only", base_url="https://provider.invalid/v1",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        )
        client = PydanticAIModelClient(
            OpenAIChatModel("minimax-m3", provider=OpenAIProvider(openai_client=openai_client)),
            ModelProfileSpec(
                provider_id="volcengine", model_id="minimax-m3",
                context_window_tokens=8192, max_output_tokens=1024, request_timeout_seconds=5.0,
            ),
            openai_client=openai_client, async_kernel=kernel,
        )
        try:
            response = client.complete(
                ({"type": "user", "content": "Edit the file."},),
                threading.Event(), lambda _delta: None,
                instructions="Use the declared tool contract.", tool_definitions=(definition,),
            )
        finally:
            client.close()
            kernel.close()
        sent = payloads[0]["tools"][0]["function"]
        # Pydantic AI strips presentation-only schema titles before sending.
        expected_schema = json.loads(json.dumps(definition.parameters_json_schema))
        expected_schema.pop("title")
        expected_schema["properties"]["patch"].pop("title")
        assert sent["parameters"] == expected_schema
        assert sent["description"] == definition.description
        assert response.tool_calls[0].arguments == {"patch": PATCH}
        entry = executor.registry.get("apply_patch")
        assert entry is not None
        assert entry.validate_arguments(response.tool_calls[0].arguments).valid
