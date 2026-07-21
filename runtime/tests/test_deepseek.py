from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch


import sys

RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.model.deepseek import (  # noqa: E402
    MAX_SSE_EVENTS,
    ModelProviderError,
    _messages_from_context,
    _read_stream,
)
from eidos_runtime.model.config import ModelConfigStore, model_catalog  # noqa: E402


class DeepSeekStreamTests(unittest.TestCase):
    def test_streams_visible_text_and_reassembles_tool_arguments(self) -> None:
        events = [
            {"choices": [{"delta": {"reasoning_content": "private"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "Checking"}, "finish_reason": None}]},
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call-1",
                                    "function": {"name": "read_file", "arguments": '{"path":'},
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "function": {"arguments": '"README.md"}'}}
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
        ]
        response = FakeResponse(_sse(events))
        deltas: list[str] = []

        result = _read_stream(response, threading.Event(), deltas.append)

        self.assertEqual(deltas, ["Checking"])
        self.assertEqual(result.text, "Checking")
        self.assertEqual(result.tool_calls[0].name, "read_file")
        self.assertEqual(result.tool_calls[0].arguments, {"path": "README.md"})

    def test_context_maps_tool_calls_and_results_without_reasoning(self) -> None:
        messages = _messages_from_context(
            (
                {"type": "user", "content": "Read it"},
                {
                    "type": "tool_call",
                    "callId": "call-1",
                    "name": "read_file",
                    "arguments": '{"path":"README.md"}',
                },
                {
                    "type": "tool_result",
                    "callId": "call-1",
                    "name": "read_file",
                    "result": '{"outcome":"success"}',
                },
            )
        )

        self.assertEqual(messages[1], {"role": "user", "content": "Read it"})
        self.assertEqual(messages[2]["role"], "assistant")
        self.assertEqual(messages[3]["role"], "tool")
        self.assertNotIn("reasoning_content", json.dumps(messages))

    def test_stream_uses_request_absolute_deadline(self) -> None:
        with self.assertRaisesRegex(ModelProviderError, "provider_timeout"):
            _read_stream(
                FakeResponse(_sse([])),
                threading.Event(),
                lambda _delta: None,
                deadline=0.0,
            )

    def test_stream_refreshes_socket_timeout_before_every_blocking_read(self) -> None:
        clock = [1.0]
        response = AdvancingResponse(
            [
                _sse_line(
                    {"choices": [{"delta": {"content": "ok"}, "finish_reason": None}]}
                ),
                _sse_line(
                    {"choices": [{"delta": {}, "finish_reason": "stop"}]}
                ),
                b"data: [DONE]\n",
            ],
            clock,
            advances=(7.0, 1.5, 0.0),
        )
        read_timeouts: list[float] = []

        with patch("eidos_runtime.model.deepseek.time.monotonic", side_effect=lambda: clock[0]):
            result = _read_stream(
                response,
                threading.Event(),
                lambda _delta: None,
                deadline=10.0,
                set_read_timeout=read_timeouts.append,
            )

        self.assertEqual(result.text, "ok")
        self.assertEqual(read_timeouts, [9.0, 2.0, 0.5])

    def test_stream_rejects_an_oversized_accumulated_tool_name(self) -> None:
        response = FakeResponse(
            _sse(
                [
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call-1",
                                            "function": {
                                                "name": "x" * 257,
                                                "arguments": "{}",
                                            },
                                        }
                                    ]
                                },
                                "finish_reason": "tool_calls",
                            }
                        ]
                    }
                ]
            )
        )

        with self.assertRaisesRegex(ModelProviderError, "provider_response_too_large"):
            _read_stream(response, threading.Event(), lambda _delta: None)

    def test_stream_rejects_too_many_sse_events(self) -> None:
        empty = {"choices": [{"delta": {}, "finish_reason": None}]}
        final = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
        response = FakeResponse(_sse([empty] * MAX_SSE_EVENTS + [final]))

        with self.assertRaisesRegex(ModelProviderError, "provider_response_too_large"):
            _read_stream(response, threading.Event(), lambda _delta: None)


class ModelConfigStoreTests(unittest.TestCase):
    def test_model_catalog_lists_flash_then_pro_and_defaults_to_flash(self) -> None:
        self.assertEqual(
            model_catalog(configured=True),
            {
                "models": [
                    {
                        "id": "deepseek-v4-flash",
                        "provider": "deepseek",
                        "displayName": "DeepSeek V4 Flash",
                        "configured": True,
                        "selectable": True,
                    },
                    {
                        "id": "deepseek-v4-pro",
                        "provider": "deepseek",
                        "displayName": "DeepSeek V4 Pro",
                        "configured": True,
                        "selectable": True,
                    },
                ],
                "defaultModelId": "deepseek-v4-flash",
            },
        )

    def test_saves_private_configuration_and_never_returns_the_key_in_status(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-model-") as temporary:
            directory = Path(temporary)
            os.chmod(directory, 0o700)
            store = ModelConfigStore(directory)
            store.initialize()

            store.save_api_key("sk-example-key-for-tests")

            self.assertEqual(store.api_key(), "sk-example-key-for-tests")
            self.assertEqual(
                store.public_status(),
                {
                    "provider": "deepseek",
                    "model": "deepseek-v4-flash",
                    "configured": True,
                },
            )
            self.assertNotIn("apiKey", store.public_status())
            self.assertEqual(
                stat.S_IMODE((directory / "model.json").stat().st_mode), 0o600
            )


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.stream = BytesIO(payload)

    def readline(self, limit: int) -> bytes:
        return self.stream.readline(limit)


class AdvancingResponse:
    def __init__(
        self,
        lines: list[bytes],
        clock: list[float],
        *,
        advances: tuple[float, ...],
    ) -> None:
        self.lines = iter(lines)
        self.clock = clock
        self.advances = iter(advances)

    def readline(self, _limit: int) -> bytes:
        line = next(self.lines, b"")
        self.clock[0] += next(self.advances, 0.0)
        return line


def _sse(events: list[dict[str, object]]) -> bytes:
    lines = [
        b"data: " + json.dumps(event, separators=(",", ":")).encode("utf-8") + b"\n\n"
        for event in events
    ]
    lines.append(b"data: [DONE]\n\n")
    return b"".join(lines)


def _sse_line(event: dict[str, object]) -> bytes:
    return b"data: " + json.dumps(event, separators=(",", ":")).encode("utf-8") + b"\n"


if __name__ == "__main__":
    unittest.main()
