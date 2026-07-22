from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import unittest

import httpx
from pydantic_ai import ModelRequest as PAIModelRequest
from pydantic_ai.messages import (
    ModelResponse as PAIModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import DeltaThinkingPart, DeltaToolCall, FunctionModel
from pydantic_ai.usage import RequestUsage
from pydantic_ai.exceptions import (
    IncompleteToolCall,
    ModelHTTPError,
    UnexpectedModelBehavior,
)


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.model.client import (  # noqa: E402
    ModelRequestError,
    ModelToolDefinition,
)
from eidos_runtime.model.config import (  # noqa: E402
    ModelConfigStore,
    ModelProfileSpec,
    model_catalog,
)
from eidos_runtime.model.prompts import SYSTEM_PROMPT  # noqa: E402
from eidos_runtime.model.pydantic_ai_client import (  # noqa: E402
    ModelClientFactory,
    PydanticAIModelClient,
    _cancel_when_requested,
    encode_context,
    map_model_error,
    map_model_response,
)


class PydanticAIModelClientTests(unittest.TestCase):
    def client(self, stream_function) -> PydanticAIModelClient:
        model = FunctionModel(stream_function=stream_function, model_name="fixture")
        client = PydanticAIModelClient(
            model,
            ModelProfileSpec(
                provider_id="function",
                model_id="fixture",
                context_window_tokens=4_096,
                max_output_tokens=512,
                request_timeout_seconds=5.0,
            ),
        )
        self.addCleanup(client.close)
        return client

    def test_streams_multiple_text_deltas_and_ignores_thinking(self) -> None:
        async def stream(_messages, _info):
            yield {0: DeltaThinkingPart(content="private")}
            yield {0: DeltaThinkingPart(content=" reasoning")}
            yield "Hel"
            yield "lo"

        deltas: list[str] = []
        response = self.client(stream).complete(
            ({"type": "user", "content": "hi"},),
            threading.Event(),
            deltas.append,
        )

        self.assertEqual(deltas, ["Hel", "lo"])
        self.assertEqual(response.text, "Hello")
        self.assertNotIn("private", response.text)
        self.assertEqual(response.resolved_model_name, "fixture")

    def test_profile_capabilities_come_from_resolved_model_profile(self) -> None:
        profile = self.client(_one_chunk).profile_snapshot

        self.assertTrue(profile.supports_tools)
        self.assertTrue(profile.supports_json_schema_output)
        self.assertFalse(profile.supports_reasoning)

    def test_reassembles_fragmented_and_multiple_tool_calls(self) -> None:
        async def stream(_messages, _info):
            yield {0: DeltaToolCall(name="read_file", json_args='{"path":')}
            yield {0: DeltaToolCall(json_args='"a.txt"}', tool_call_id="call-1")}
            yield {1: DeltaToolCall(
                name="list_files", json_args='{"path":"."}', tool_call_id="call-2"
            )}

        response = self.client(stream).complete((), threading.Event(), lambda _delta: None)

        self.assertEqual(
            [(call.provider_call_id, call.name, call.arguments) for call in response.tool_calls],
            [
                ("call-1", "read_file", {"path": "a.txt"}),
                ("call-2", "list_files", {"path": "."}),
            ],
        )

    def test_missing_tool_call_id_uses_pydantic_generated_stable_id(self) -> None:
        async def stream(_messages, _info):
            yield {0: DeltaToolCall(name="read_file", json_args='{"path":"a.txt"}')}

        response = self.client(stream).complete((), threading.Event(), lambda _delta: None)

        self.assertTrue(response.tool_calls[0].provider_call_id.startswith("pyd_ai_"))

    def test_invalid_tool_arguments_map_to_protocol_error(self) -> None:
        async def stream(_messages, _info):
            yield {0: DeltaToolCall(name="read_file", json_args='["not-object"]')}

        with self.assertRaises(ModelRequestError) as raised:
            self.client(stream).complete((), threading.Event(), lambda _delta: None)
        self.assertEqual(raised.exception.failure.code, "protocol_error")
        self.assertFalse(raised.exception.failure.retryable)

    def test_context_and_tool_definitions_use_public_pydantic_types(self) -> None:
        messages = encode_context((
            {"type": "user", "content": "你好"},
            {"type": "assistant", "content": ""},
            {
                "type": "tool_call", "callId": "call-1", "name": "read_file",
                "arguments": '{"path":"README.md"}',
            },
            {
                "type": "tool_result", "callId": "call-1", "name": "read_file",
                "result": '{"outcome":"success"}',
            },
            {"type": "protocol_error", "code": "invalid_tool_call"},
        ))

        self.assertIsInstance(messages[0], PAIModelRequest)
        self.assertEqual(messages[0].instructions, SYSTEM_PROMPT)
        self.assertIsInstance(messages[0].parts[0], UserPromptPart)
        self.assertIsInstance(messages[2], PAIModelResponse)
        self.assertIsInstance(messages[2].parts[0], ToolCallPart)
        self.assertIsInstance(messages[3].parts[0], ToolReturnPart)
        self.assertIsInstance(messages[4].parts[0], UserPromptPart)

        captured = {}
        async def stream(_messages, info):
            captured["tools"] = info.function_tools
            captured["instructions"] = info.instructions
            yield "done"

        definition = ModelToolDefinition(
            name="read_file",
            description="Read a file.",
            parameters_json_schema={
                "type": "object", "properties": {}, "additionalProperties": False,
            },
        )
        self.client(stream).complete(
            ({"type": "user", "content": "read"},),
            threading.Event(),
            lambda _delta: None,
            tool_definitions=(definition,),
        )
        self.assertEqual(captured["tools"][0].name, "read_file")
        self.assertEqual(captured["instructions"], SYSTEM_PROMPT)

    def test_maps_usage_finish_provider_response_id_and_state(self) -> None:
        response = map_model_response(PAIModelResponse(
            parts=[TextPart("done"), ThinkingPart("private")],
            usage=RequestUsage(
                input_tokens=10,
                output_tokens=4,
                cache_read_tokens=2,
                cache_write_tokens=1,
                details={"reasoning_tokens": 3},
            ),
            model_name="resolved",
            provider_name="fixture-provider",
            provider_response_id="response-1",
            finish_reason="length",
            state="complete",
        ))
        self.assertEqual(response.text, "done")
        self.assertEqual(response.usage.input_tokens, 10)
        self.assertEqual(response.finish_reason, "length")
        self.assertEqual(response.provider_name, "fixture-provider")
        self.assertEqual(response.provider_response_id, "response-1")
        self.assertEqual(response.response_state, "complete")

    def test_error_mapping_is_stable_and_drops_raw_bodies(self) -> None:
        expected = {
            400: ("invalid_request", False),
            401: ("authentication_failed", False),
            403: ("authentication_failed", False),
            408: ("provider_unavailable", True),
            413: ("context_exceeded", False),
            422: ("invalid_request", False),
            425: ("provider_unavailable", True),
            429: ("rate_limited", True),
            500: ("provider_unavailable", True),
            502: ("provider_unavailable", True),
            503: ("provider_unavailable", True),
            504: ("provider_unavailable", True),
        }
        for status, value in expected.items():
            with self.subTest(status=status):
                failure = map_model_error(ModelHTTPError(
                    status, "fixture", {"secret": "do not persist"}
                ))
                self.assertEqual((failure.code, failure.retryable), value)
                self.assertNotIn("secret", failure.model_dump_json())

        self.assertEqual(
            map_model_error(httpx.ReadTimeout("timeout")).code,
            "provider_timeout",
        )
        self.assertEqual(
            map_model_error(httpx.NetworkError("network")).code,
            "provider_unavailable",
        )
        self.assertEqual(
            map_model_error(UnexpectedModelBehavior("malformed")).code,
            "protocol_error",
        )
        incomplete = map_model_error(IncompleteToolCall("partial", "raw-secret"))
        self.assertEqual(incomplete.code, "protocol_error")
        self.assertNotIn("raw-secret", incomplete.model_dump_json())

    def test_explicit_cancel_does_not_become_provider_error(self) -> None:
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(ModelRequestError) as raised:
            self.client(_one_chunk).complete((), cancel, lambda _delta: None)
        self.assertEqual(raised.exception.failure.code, "sampling_canceled")

    def test_cancel_bridge_calls_public_stream_cancel(self) -> None:
        class CancelableStream:
            canceled = False

            async def cancel(self) -> None:
                self.canceled = True

        stream = CancelableStream()
        cancel = threading.Event()
        cancel.set()
        asyncio.run(_cancel_when_requested(cancel, stream))  # type: ignore[arg-type]
        self.assertTrue(stream.canceled)

    def test_client_remains_reusable_after_cancelled_call(self) -> None:
        client = self.client(_one_chunk)
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(ModelRequestError):
            client.complete((), cancel, lambda _delta: None)

        response = client.complete((), threading.Event(), lambda _delta: None)
        self.assertEqual(response.text, "done")

    def test_factory_disables_sdk_retries_reuses_clients_and_closes_threads(self) -> None:
        factory = ModelClientFactory("sk-example-key-for-tests")
        first = factory.client_for("deepseek-v4-flash")
        self.assertIs(first, factory.client_for("deepseek-v4-flash"))
        self.assertIsNot(first, factory.client_for("deepseek-v4-pro"))
        self.assertEqual(first.sdk_max_retries, 0)
        names = {thread.name for thread in threading.enumerate()}
        self.assertTrue(any(name.startswith("eidos-model-") for name in names))
        factory.close()
        names = {thread.name for thread in threading.enumerate()}
        self.assertFalse(any(name.startswith("eidos-model-") for name in names))


async def _one_chunk(_messages, _info):
    yield "done"


class ModelConfigStoreTests(unittest.TestCase):
    def test_model_catalog_lists_flash_then_pro_and_defaults_to_flash(self) -> None:
        self.assertEqual(
            [model["id"] for model in model_catalog(configured=True)["models"]],
            ["deepseek-v4-flash", "deepseek-v4-pro"],
        )

    def test_saves_private_configuration_and_never_returns_the_key_in_status(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-model-") as temporary:
            directory = Path(temporary)
            os.chmod(directory, 0o700)
            store = ModelConfigStore(directory)
            store.initialize()
            store.save_api_key("sk-example-key-for-tests")
            self.assertNotIn("apiKey", store.public_status())
            self.assertEqual(
                stat.S_IMODE((directory / "model.json").stat().st_mode), 0o600
            )


if __name__ == "__main__":
    unittest.main()
