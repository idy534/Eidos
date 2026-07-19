from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import threading
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel  # noqa: E402
from eidos_runtime.runtime.model_runner import ModelRunner  # noqa: E402
from eidos_runtime.runtime.tool_dispatcher import ToolDispatcher  # noqa: E402
from eidos_runtime.runtime.approval import ApprovalAdapter, ApprovalRequest  # noqa: E402
from eidos_runtime.runtime.events import RuntimeEvents  # noqa: E402
from eidos_runtime.tools.workspace import ToolExecutor  # noqa: E402


class RuntimeSeamTests(unittest.TestCase):
    def test_model_runner_releases_safe_lines_before_model_completion(self) -> None:
        observed_during_completion: list[list[str]] = []
        deltas: list[str] = []

        class StreamingModel:
            def complete(self, _context, _cancel, on_text_delta, **_kwargs):
                on_text_delta("first line\n")
                observed_during_completion.append(list(deltas))
                on_text_delta("second line")
                return ModelResponse(text="first line\nsecond line")

        result = ModelRunner(StreamingModel()).run(
            (), threading.Event(), deltas.append
        )

        self.assertEqual(observed_during_completion, [["first line\n"]])
        self.assertEqual(deltas, ["first line\n", "second line"])
        self.assertEqual(result.text, "first line\nsecond line")

    def test_model_runner_returns_visible_text_and_tool_calls(self) -> None:
        model = ScriptedModel([
            ModelResponse(
                text="hello",
                tool_calls=(ModelToolCall("call-1", "read_file", {"path": "a.txt"}),),
            )
        ])
        deltas: list[str] = []

        result = ModelRunner(model).run((), threading.Event(), deltas.append)

        self.assertEqual(result.text, "hello")
        self.assertEqual(result.tool_calls[0].name, "read_file")
        self.assertEqual(deltas, ["hello"])

    def test_model_runner_passes_the_step_tool_definitions_explicitly(self) -> None:
        model = ScriptedModel([ModelResponse(text="done")])
        definitions = ({
            "type": "function",
            "function": {
                "name": "memory_echo",
                "description": "Echo.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
        },)

        ModelRunner(model).run(
            (), threading.Event(), lambda _delta: None,
            tool_definitions=definitions,
        )

        self.assertEqual(model.tool_definitions_history, [definitions])

    def test_tool_dispatcher_returns_one_closed_batch_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tools = ToolExecutor(Path(directory))
            try:
                result = ToolDispatcher(tools.registry).validate(ModelResponse(tool_calls=(
                    ModelToolCall("first", "read_file", {"path": "a.txt"}),
                    ModelToolCall("second", "write_file", {"path": "a.txt", "content": "x"}),
                )))
            finally:
                tools.close()

        self.assertEqual(result.error_code, "invalid_tool_batch")

    def test_tool_dispatcher_executes_a_read_only_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.txt").write_text("ok", encoding="utf-8")
            tools = ToolExecutor(root)
            try:
                result = ToolDispatcher(tools.registry).execute_read_only(
                    ModelToolCall("read", "read_file", {"path": "a.txt"}),
                    threading.Event(),
                )
            finally:
                tools.close()

        self.assertEqual(result["outcome"], "success")

    def test_tool_dispatcher_classifies_shell_as_approval_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tools = ToolExecutor(Path(directory))
            try:
                plan = ToolDispatcher(tools.registry).plan(
                    ModelToolCall("shell", "run_shell", {"command": "true"})
                )
            finally:
                tools.close()

        self.assertEqual((plan.requires_approval, plan.is_shell), (True, True))

    def test_approval_adapter_preserves_rejection_feedback(self) -> None:
        class Decision:
            decision = "reject"
            feedback = "try another path"

        result = ApprovalAdapter(lambda _payload, _cancel: Decision()).request(
            ApprovalRequest({"kind": "file_change"}), threading.Event()
        )

        self.assertEqual((result.decision, result.feedback), ("reject", "try another path"))

    def test_runtime_events_preserves_the_notification_envelope(self) -> None:
        messages: list[dict[str, object]] = []

        RuntimeEvents(messages.append).emit("run/updated", {"run": {"id": "run"}})

        self.assertEqual(messages, [{"jsonrpc": "2.0", "method": "run/updated", "params": {"run": {"id": "run"}}}])


if __name__ == "__main__":
    unittest.main()
