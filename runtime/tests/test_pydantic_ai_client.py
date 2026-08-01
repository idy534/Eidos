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
from unittest.mock import patch

import httpx
from openai import AsyncOpenAI
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
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.deepseek import DeepSeekProvider
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
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel  # noqa: E402


class PydanticAIModelClientTests(unittest.TestCase):
    def client(self, stream_function) -> PydanticAIModelClient:
        model = FunctionModel(stream_function=stream_function, model_name="fixture")
        kernel = RuntimeAsyncKernel()
        kernel.start()
        self.addCleanup(kernel.close)
        client = PydanticAIModelClient(
            model,
            ModelProfileSpec(
                provider_id="function",
                model_id="fixture",
                context_window_tokens=4_096,
                max_output_tokens=512,
                request_timeout_seconds=5.0,
            ),
            async_kernel=kernel,
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

    def test_stream_callbacks_are_serialized_off_the_kernel_event_loop(self) -> None:
        event_loop_thread_ids: list[int] = []
        callback_thread_ids: list[int] = []
        callback_deltas: list[str] = []
        active_callbacks = 0
        maximum_active_callbacks = 0
        callback_lock = threading.Lock()

        async def stream(_messages, _info):
            event_loop_thread_ids.append(threading.get_ident())
            yield "one"
            yield "two"

        def on_delta(delta: str) -> None:
            nonlocal active_callbacks, maximum_active_callbacks
            with callback_lock:
                active_callbacks += 1
                maximum_active_callbacks = max(maximum_active_callbacks, active_callbacks)
                callback_thread_ids.append(threading.get_ident())
                callback_deltas.append(delta)
            try:
                self.assertIn(delta, {"one", "two"})
            finally:
                with callback_lock:
                    active_callbacks -= 1

        response = self.client(stream).complete((), threading.Event(), on_delta)

        self.assertEqual(response.text, "onetwo")
        self.assertEqual(callback_deltas, ["one", "two"])
        self.assertEqual(maximum_active_callbacks, 1)
        self.assertEqual(len(callback_thread_ids), 2)
        self.assertTrue(all(
            callback_thread_id != event_loop_thread_ids[0]
            for callback_thread_id in callback_thread_ids
        ))

    def test_cancel_waits_for_the_in_flight_stream_callback(self) -> None:
        callback_entered = threading.Event()
        release_callback = threading.Event()
        cancel = threading.Event()
        errors: list[BaseException] = []

        async def stream(_messages, _info):
            yield "first"

        def on_delta(_delta: str) -> None:
            callback_entered.set()
            release_callback.wait(timeout=1.0)

        client = self.client(stream)

        def complete() -> None:
            try:
                client.complete((), cancel, on_delta)
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=complete)
        worker.start()
        self.assertTrue(callback_entered.wait(timeout=1.0))
        cancel.set()
        self.assertTrue(worker.is_alive())
        release_callback.set()
        worker.join(timeout=1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ModelRequestError)
        self.assertEqual(errors[0].failure.code, "sampling_canceled")

    def test_stream_callback_error_maps_to_the_existing_model_error_path(self) -> None:
        async def stream(_messages, _info):
            yield "first"

        def fail_callback(_delta: str) -> None:
            raise ValueError("writer failed")

        with self.assertRaises(ModelRequestError) as raised:
            self.client(stream).complete((), threading.Event(), fail_callback)

        self.assertEqual(raised.exception.failure.code, "protocol_error")

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
            {"type": "finalization", "stopReason": "repeated_tool_call"},
        ))

        self.assertIsInstance(messages[0], PAIModelRequest)
        self.assertEqual(messages[0].instructions, SYSTEM_PROMPT)
        self.assertIsInstance(messages[0].parts[0], UserPromptPart)
        self.assertIsInstance(messages[2], PAIModelResponse)
        self.assertIsInstance(messages[2].parts[0], ToolCallPart)
        self.assertIsInstance(messages[3].parts[0], ToolReturnPart)
        self.assertIsInstance(messages[4].parts[0], UserPromptPart)
        self.assertIn("manual strategy", messages[5].parts[0].content)

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

    def test_cancel_stops_an_idle_stream_without_waiting_for_an_event(self) -> None:
        class IdleStream:
            def __init__(self) -> None:
                self.entered = threading.Event()
                self.canceled = threading.Event()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args) -> None:
                return None

            def __aiter__(self):
                return self.events()

            async def events(self):
                self.entered.set()
                while not self.canceled.is_set():
                    await asyncio.sleep(0.001)
                if False:
                    yield None

            async def cancel(self) -> None:
                self.canceled.set()

            def get(self):
                return PAIModelResponse(parts=[])

        stream = IdleStream()
        cancel = threading.Event()
        result: list[BaseException] = []
        client = self.client(_one_chunk)

        def complete() -> None:
            try:
                client.complete((), cancel, lambda _delta: None)
            except BaseException as error:
                result.append(error)

        with patch(
            "eidos_runtime.model.pydantic_ai_client.model_request_stream",
            return_value=stream,
        ):
            worker = threading.Thread(target=complete)
            worker.start()
            self.assertTrue(stream.entered.wait(timeout=1.0))
            cancel.set()
            self.assertTrue(stream.canceled.wait(timeout=1.0))
            worker.join(timeout=1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], ModelRequestError)
        self.assertEqual(result[0].failure.code, "sampling_canceled")

    def test_client_remains_reusable_after_cancelled_call(self) -> None:
        client = self.client(_one_chunk)
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(ModelRequestError):
            client.complete((), cancel, lambda _delta: None)

        response = client.complete((), threading.Event(), lambda _delta: None)
        self.assertEqual(response.text, "done")

    def test_clients_share_kernel_and_client_close_does_not_stop_title_generation(self) -> None:
        kernel = RuntimeAsyncKernel()
        kernel.start()
        profile = ModelProfileSpec(
            provider_id="function",
            model_id="fixture",
            context_window_tokens=4_096,
            max_output_tokens=512,
            request_timeout_seconds=5.0,
        )
        first = PydanticAIModelClient(
            FunctionModel(stream_function=_one_chunk, model_name="first"),
            profile,
            async_kernel=kernel,
        )
        second = PydanticAIModelClient(
            FunctionModel(stream_function=_one_chunk, model_name="second"),
            profile,
            async_kernel=kernel,
        )
        try:
            first.close()
            self.assertEqual(
                second.generate_title("name this session", threading.Event()),
                "done",
            )
            self.assertEqual(
                second.complete((), threading.Event(), lambda _delta: None).text,
                "done",
            )
            self.assertFalse(any(
                thread.name.startswith("eidos-model-")
                for thread in threading.enumerate()
            ))
        finally:
            second.close()
            kernel.close()

    def test_factory_disables_sdk_retries_reuses_clients_without_model_threads(self) -> None:
        kernel = RuntimeAsyncKernel()
        kernel.start()
        factory = ModelClientFactory(
            "sk-example-key-for-tests", async_kernel=kernel
        )
        first = factory.client_for("deepseek-v4-flash")
        self.assertIs(first, factory.client_for("deepseek-v4-flash"))
        self.assertIsNot(first, factory.client_for("deepseek-v4-pro"))
        self.assertEqual(first.sdk_max_retries, 0)
        names = {thread.name for thread in threading.enumerate()}
        self.assertFalse(any(name.startswith("eidos-model-") for name in names))
        factory.close()
        kernel.close()

    def test_deepseek_request_omits_unsupported_none_reasoning_effort(self) -> None:
        payloads: list[dict[str, object]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            payloads.append(json.loads(request.content))
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=(
                    b'data: {"id":"response-1","object":"chat.completion.chunk",'
                    b'"created":0,"model":"deepseek-v4-flash",'
                    b'"choices":[{"index":0,"delta":{"content":"OK"},'
                    b'"finish_reason":null}]}\n\n'
                    b'data: {"id":"response-1","object":"chat.completion.chunk",'
                    b'"created":0,"model":"deepseek-v4-flash",'
                    b'"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
                    b'data: [DONE]\n\n'
                ),
            )

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        openai_client = AsyncOpenAI(
            api_key="sk-example-key-for-tests",
            base_url="https://api.deepseek.com",
            http_client=http_client,
        )
        kernel = RuntimeAsyncKernel()
        kernel.start()
        client = PydanticAIModelClient(
            OpenAIChatModel(
                "deepseek-v4-flash",
                provider=DeepSeekProvider(openai_client=openai_client),
            ),
            ModelProfileSpec(
                provider_id="deepseek",
                model_id="deepseek-v4-flash",
                context_window_tokens=802_816,
                max_output_tokens=8_192,
                request_timeout_seconds=120.0,
            ),
            openai_client=openai_client,
            async_kernel=kernel,
        )
        self.addCleanup(kernel.close)
        self.addCleanup(client.close)
        self.addCleanup(lambda: asyncio.run(http_client.aclose()))

        response = client.complete(
            ({"type": "user", "content": "Reply with OK."},),
            threading.Event(),
            lambda _delta: None,
            allow_tools=False,
        )

        self.assertEqual(response.text, "OK")
        self.assertNotIn("reasoning_effort", payloads[0])
        self.assertEqual(payloads[0]["thinking"], {"type": "disabled"})


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
