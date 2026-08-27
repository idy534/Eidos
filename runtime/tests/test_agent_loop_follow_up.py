from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.model.client import (  # noqa: E402
    AssistantMessagePhase,
    ModelResponse,
    ModelToolCall,
    ScriptedModel,
)
from eidos_runtime.runtime.loop import RuntimeLoop  # noqa: E402


class AgentLoopFollowUpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-follow-up-")
        root = Path(self.temporary.name)
        self.data = root / "data"
        self.workspace = root / "workspace"
        self.data.mkdir(mode=0o700)
        self.workspace.mkdir()
        (self.workspace / "hello.txt").write_text(
            "hello from workspace\n", encoding="utf-8"
        )
        self.store = SessionStore(self.data)
        self.store.initialize()
        self.session = self.store.create_session(str(self.workspace))

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _run(
        self,
        user_input: str,
        responses: list[ModelResponse],
    ) -> tuple[dict[str, object], ScriptedModel, dict[str, object]]:
        run, _ = self.store.create_run(self.session["id"], user_input)
        model = ScriptedModel(responses)
        RuntimeLoop(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )
        return run, model, self.store.read_session_snapshot(self.session["id"])

    def test_tool_call_result_is_followed_by_assistant_only_sampling(self) -> None:
        _run, model, snapshot = self._run(
            "Read hello.txt",
            [
                ModelResponse(
                    phase=AssistantMessagePhase.UNKNOWN,
                    tool_calls=(
                        ModelToolCall(
                            "call-read",
                            "read_file",
                            {"path": "hello.txt"},
                        ),
                    ),
                ),
                ModelResponse(
                    text="The file says hello.",
                    phase=None,
                ),
            ],
        )

        self.assertEqual(len(model.contexts), 2)
        self.assertEqual(snapshot["runs"][0]["status"], "succeeded")
        tool_item = next(item for item in snapshot["items"] if item["kind"] == "tool_call")
        tool_result = json.loads(tool_item["toolCall"]["resultJson"])
        self.assertEqual(tool_result["outcome"], "success")
        self.assertEqual(tool_result["data"]["content"], "hello from workspace\n")
        context_tool_results = [
            entry for entry in model.contexts[1] if entry.get("type") == "tool_result"
        ]
        self.assertEqual(len(context_tool_results), 1)
        self.assertEqual(context_tool_results[0]["name"], "read_file")
        self.assertIn("hello from workspace", str(context_tool_results[0]["result"]))
        self.assertEqual(
            [item.get("content") for item in snapshot["items"] if item["kind"] == "assistant_message"],
            ["The file says hello."],
        )

    def test_assistant_progress_with_tool_call_does_not_end_turn(self) -> None:
        _run, model, snapshot = self._run(
            "Inspect hello.txt",
            [
                ModelResponse(
                    text="I will inspect the file first.",
                    phase=AssistantMessagePhase.UNKNOWN,
                    finish_reason="tool_calls",
                    tool_calls=(
                        ModelToolCall(
                            "call-search",
                            "search_text",
                            {"query": "hello"},
                        ),
                    ),
                ),
                ModelResponse(
                    text="The inspection is complete.",
                    phase=AssistantMessagePhase.UNKNOWN,
                    finish_reason="stop",
                ),
            ],
        )

        self.assertEqual(len(model.contexts), 2)
        self.assertEqual(snapshot["runs"][0]["status"], "succeeded")
        self.assertEqual(
            [item["kind"] for item in snapshot["items"]],
            [
                "user_message",
                "assistant_message",
                "tool_call",
                "assistant_message",
            ],
        )
        self.assertEqual(
            [item.get("content") for item in snapshot["items"] if item["kind"] == "assistant_message"],
            ["I will inspect the file first.", "The inspection is complete."],
        )

    def test_tool_failure_is_returned_to_context_before_next_tool_call(self) -> None:
        _run, model, snapshot = self._run(
            "Recover from a missing file",
            [
                ModelResponse(
                    phase=AssistantMessagePhase.UNKNOWN,
                    finish_reason="tool_calls",
                    tool_calls=(
                        ModelToolCall(
                            "call-missing",
                            "read_file",
                            {"path": "missing.txt"},
                        ),
                    ),
                ),
                ModelResponse(
                    phase=AssistantMessagePhase.UNKNOWN,
                    finish_reason="tool_calls",
                    tool_calls=(
                        ModelToolCall(
                            "call-recovery",
                            "read_file",
                            {"path": "hello.txt"},
                        ),
                    ),
                ),
                ModelResponse(
                    text="I recovered by reading the available file.",
                    phase=None,
                    finish_reason="stop",
                ),
            ],
        )

        self.assertEqual(len(model.contexts), 3)
        self.assertEqual(snapshot["runs"][0]["status"], "succeeded")
        tool_items = [item for item in snapshot["items"] if item["kind"] == "tool_call"]
        self.assertEqual([item["toolCall"]["providerCallId"] for item in tool_items], [
            "call-missing",
            "call-recovery",
        ])
        failed_result = json.loads(tool_items[0]["toolCall"]["resultJson"])
        recovered_result = json.loads(tool_items[1]["toolCall"]["resultJson"])
        self.assertNotEqual(failed_result["outcome"], "success")
        self.assertEqual(recovered_result["outcome"], "success")

        first_follow_up_results = [
            entry for entry in model.contexts[1] if entry.get("type") == "tool_result"
        ]
        second_follow_up_results = [
            entry for entry in model.contexts[2] if entry.get("type") == "tool_result"
        ]
        self.assertEqual(
            [entry["callId"] for entry in first_follow_up_results], ["call-missing"]
        )
        self.assertEqual(
            [entry["callId"] for entry in second_follow_up_results],
            ["call-missing", "call-recovery"],
        )
        self.assertNotEqual(
            json.loads(first_follow_up_results[0]["result"])["outcome"],
            "success",
        )
        self.assertEqual(
            json.loads(second_follow_up_results[1]["result"])["outcome"],
            "success",
        )

    def test_assistant_only_response_can_finish_after_tool_failure(self) -> None:
        _run, model, snapshot = self._run(
            "Try a missing file and then answer",
            [
                ModelResponse(
                    phase=AssistantMessagePhase.UNKNOWN,
                    finish_reason="tool_calls",
                    tool_calls=(
                        ModelToolCall(
                            "call-missing",
                            "read_file",
                            {"path": "missing.txt"},
                        ),
                    ),
                ),
                ModelResponse(
                    text="The requested file is not present.",
                    phase=None,
                    finish_reason="stop",
                ),
            ],
        )

        self.assertEqual(len(model.contexts), 2)
        self.assertEqual(snapshot["runs"][0]["status"], "succeeded")
        self.assertEqual(
            [item.get("content") for item in snapshot["items"] if item["kind"] == "assistant_message"],
            ["The requested file is not present."],
        )
        failed_tool = next(item for item in snapshot["items"] if item["kind"] == "tool_call")
        failed_result = json.loads(failed_tool["toolCall"]["resultJson"])
        self.assertNotEqual(failed_result["outcome"], "success")

    def test_assistant_only_unknown_phase_can_finish_turn(self) -> None:
        run, model, snapshot = self._run(
            "Answer directly without tools",
            [
                ModelResponse(
                    text="This answer has no declared message phase.",
                    phase=AssistantMessagePhase.UNKNOWN,
                ),
            ],
        )

        self.assertEqual(len(model.contexts), 1)
        self.assertEqual(snapshot["runs"][0]["status"], "succeeded")
        self.assertEqual(
            [item.get("content") for item in snapshot["items"] if item["kind"] == "assistant_message"],
            ["This answer has no declared message phase."],
        )
        attempts = self.store.read_model_attempts(run["id"])
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["status"], "completed")

    def test_stop_finish_reason_does_not_require_final_answer_phase(self) -> None:
        run, model, snapshot = self._run(
            "Answer with a provider stop",
            [
                ModelResponse(
                    text="The provider ended this assistant response.",
                    phase=AssistantMessagePhase.UNKNOWN,
                    finish_reason="stop",
                ),
            ],
        )

        self.assertEqual(len(model.contexts), 1)
        self.assertEqual(snapshot["runs"][0]["status"], "succeeded")
        self.assertEqual(
            [item.get("content") for item in snapshot["items"] if item["kind"] == "assistant_message"],
            ["The provider ended this assistant response."],
        )
        attempts = self.store.read_model_attempts(run["id"])
        self.assertEqual(attempts[0]["finishReason"], "stop")
        self.assertEqual(attempts[0]["status"], "completed")

    def test_tool_calls_finish_reason_is_a_follow_up(self) -> None:
        run, model, snapshot = self._run(
            "Read hello.txt with a tool call finish reason",
            [
                ModelResponse(
                    phase=AssistantMessagePhase.UNKNOWN,
                    finish_reason="tool_calls",
                    tool_calls=(
                        ModelToolCall(
                            "call-read",
                            "read_file",
                            {"path": "hello.txt"},
                        ),
                    ),
                ),
                ModelResponse(
                    text="The file was read successfully.",
                    phase=None,
                    finish_reason="stop",
                ),
            ],
        )

        self.assertEqual(len(model.contexts), 2)
        self.assertEqual(snapshot["runs"][0]["status"], "succeeded")
        tool_item = next(item for item in snapshot["items"] if item["kind"] == "tool_call")
        self.assertEqual(tool_item["status"], "completed")
        attempts = self.store.read_model_attempts(run["id"])
        self.assertEqual(attempts[0]["finishReason"], "tool_calls")
        self.assertEqual(attempts[0]["status"], "completed")

    def test_length_finish_reason_remains_an_error(self) -> None:
        run, model, snapshot = self._run(
            "Handle a truncated response",
            [
                ModelResponse(
                    text="truncated",
                    phase=AssistantMessagePhase.UNKNOWN,
                    finish_reason="length",
                    response_state="complete",
                ),
            ],
        )

        self.assertEqual(len(model.contexts), 1)
        self.assertEqual(snapshot["runs"][0]["status"], "failed")
        self.assertEqual(snapshot["runs"][0]["errorCode"], "MODEL_PROTOCOL_ERROR")
        self.assertEqual(
            [item["kind"] for item in snapshot["items"]], ["user_message"]
        )
        attempts = self.store.read_model_attempts(run["id"])
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["status"], "failed")
        self.assertEqual(attempts[0]["finishReason"], "length")
        self.assertEqual(attempts[0]["errorCode"], "length")
        self.assertEqual(attempts[0]["retryDecision"]["reason"], "invalid_completion")


if __name__ == "__main__":
    unittest.main()
