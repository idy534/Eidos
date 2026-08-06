from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import threading
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.model.client import (  # noqa: E402
    ModelResponse,
    ModelToolCall,
    ModelToolDefinition,
    ScriptedModel,
)
from eidos_runtime.runtime.model_runner import ModelRunner  # noqa: E402
from eidos_runtime.runtime.tool_dispatcher import ToolDispatcher  # noqa: E402
from eidos_runtime.runtime.approval import ApprovalAdapter, ApprovalRequest  # noqa: E402
from eidos_runtime.runtime.events import RuntimeEvents  # noqa: E402
from eidos_runtime.db.storage import CommittedMutation  # noqa: E402
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
            (),
            threading.Event(),
            deltas.append,
            instructions="resolved instructions",
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

        result = ModelRunner(model).run(
            (),
            threading.Event(),
            deltas.append,
            instructions="resolved instructions",
        )

        self.assertEqual(result.text, "hello")
        self.assertEqual(result.tool_calls[0].name, "read_file")
        self.assertEqual(deltas, ["hello"])

    def test_model_runner_passes_step_instructions_and_tools_explicitly(self) -> None:
        model = ScriptedModel([ModelResponse(text="done")])
        definitions = (ModelToolDefinition(
            name="memory_echo",
            description="Echo.",
            parameters_json_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        ),)

        ModelRunner(model).run(
            (), threading.Event(), lambda _delta: None,
            instructions="resolved step instructions",
            tool_definitions=definitions,
        )

        self.assertEqual(model.instructions_history, ["resolved step instructions"])
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

    def test_descriptor_runtime_executes_a_read_only_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.txt").write_text("ok", encoding="utf-8")
            tools = ToolExecutor(root)
            try:
                descriptor = tools.registry.get("read_file")
                assert descriptor is not None and descriptor.runtime is not None
                cancel = threading.Event()
                prepared = descriptor.runtime.prepare(
                    None, {"path": "a.txt"}, cancel
                )
                result = descriptor.runtime.execute(None, prepared, cancel)
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
        run = {"id": "run", "sessionId": "session", "status": "running"}
        event = {
            "eventId": 1,
            "eventType": "run.updated",
            "payload": {"reason": "fixture"},
        }

        RuntimeEvents(messages.append).publish(
            CommittedMutation(run, (event,)), run=run
        )

        self.assertEqual(messages, [{
            "jsonrpc": "2.0",
            "method": "run/updated",
            "params": {"sessionId": "session", "run": run},
        }])


if __name__ == "__main__":
    unittest.main()
