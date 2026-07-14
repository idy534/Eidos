from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest


import sys

RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.deepseek import _messages_from_context, _read_stream  # noqa: E402
from eidos_runtime.model_config import ModelConfigStore  # noqa: E402


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


class ModelConfigStoreTests(unittest.TestCase):
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


def _sse(events: list[dict[str, object]]) -> bytes:
    lines = [
        b"data: " + json.dumps(event, separators=(",", ":")).encode("utf-8") + b"\n\n"
        for event in events
    ]
    lines.append(b"data: [DONE]\n\n")
    return b"".join(lines)


if __name__ == "__main__":
    unittest.main()
