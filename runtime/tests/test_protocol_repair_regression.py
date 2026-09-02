from __future__ import annotations

from pathlib import Path
import hashlib
import json
import sys
import tempfile
import threading
import unittest


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.model.client import (  # noqa: E402
    AssistantMessagePhase,
    CustomToolPayload,
    ModelResponse,
    ModelToolCall,
    ScriptedModel,
)
from eidos_runtime.runtime.loop import ApprovalDecision, RuntimeLoop  # noqa: E402
from eidos_runtime.runtime.protocol_diagnostics import argument_summary  # noqa: E402


class ProtocolRepairRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-protocol-repair-")
        root = Path(self.temporary.name)
        self.data = root / "data"
        self.workspace = root / "workspace"
        self.data.mkdir(mode=0o700)
        self.workspace.mkdir()
        self.store = SessionStore(self.data)
        self.store.initialize()
        self.session = self.store.create_session(str(self.workspace))

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_multiple_workspace_mutations_are_serialized_not_rejected(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Create a Go skeleton")
        model = ScriptedModel([
            ModelResponse(
                text="I will create the files.",
                phase=AssistantMessagePhase.COMMENTARY,
                finish_reason="tool_calls",
                tool_calls=(
                    ModelToolCall(
                        "patch-go-mod",
                        "apply_patch",
                        {"changes": [{
                            "type": "add",
                            "path": "go.mod",
                            "content": "module shipping-lab\n\ngo 1.26\n",
                        }]},
                    ),
                    ModelToolCall(
                        "patch-main",
                        "apply_patch",
                        {"changes": [{
                            "type": "add",
                            "path": "main.go",
                            "content": "package main\n\nfunc main() {}\n",
                        }]},
                    ),
                ),
            ),
            ModelResponse(
                text="Created the Go project skeleton.",
                phase=AssistantMessagePhase.FINAL_ANSWER,
            ),
        ])
        approvals: list[dict[str, object]] = []

        RuntimeLoop(
            self.store,
            model,
            lambda _message: None,
            lambda request, _cancel: approvals.append(request)
            or ApprovalDecision("approve"),
        ).run(run["id"], threading.Event())

        completed = self.store.read_run(run["id"])
        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(completed["modelStepCount"], 2)
        self.assertEqual(approvals, [])
        self.assertEqual(
            (self.workspace / "go.mod").read_text(encoding="utf-8"),
            "module shipping-lab\n\ngo 1.26\n",
        )
        self.assertEqual(
            (self.workspace / "main.go").read_text(encoding="utf-8"),
            "package main\n\nfunc main() {}\n",
        )
        snapshot = self.store.read_session_snapshot(self.session["id"])
        assistant_text = [
            item.get("content")
            for item in snapshot["items"]
            if item["kind"] == "assistant_message"
        ]
        self.assertEqual(
            assistant_text,
            ["I will create the files.", "Created the Go project skeleton."],
        )
        self.assertFalse(any("DSML" in str(value) for value in assistant_text))

    def test_declared_invalid_arguments_are_tool_errors_and_model_recovers(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Repair invalid tool arguments")
        model = ScriptedModel([
            ModelResponse(
                text="I will write the file now.",
                tool_calls=(
                    ModelToolCall(
                        "bad-patch",
                        "apply_patch",
                        {"changes": []},
                    ),
                ),
            ),
            ModelResponse(
                text="Recovered after receiving the tool error.",
                phase=AssistantMessagePhase.FINAL_ANSWER,
            ),
        ])

        RuntimeLoop(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )

        completed = self.store.read_run(run["id"])
        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(completed["modelStepCount"], 2)
        attempts = self.store.read_model_attempts(run["id"])
        self.assertEqual([attempt["status"] for attempt in attempts], ["completed", "completed"])
        self.assertTrue(all(attempt["errorCode"] is None for attempt in attempts))
        self.assertEqual(len(model.contexts), 2)
        self.assertFalse(
            any(
                item.get("type") == "protocol_error"
                for context in model.contexts
                for item in context
            )
        )
        self.assertTrue(any(item.get("type") == "tool_result" for item in model.contexts[1]))
        assert self.store.connection is not None
        result = json.loads(self.store.connection.execute(
            "SELECT result_json FROM tool_calls WHERE provider_call_id = ?",
            ("bad-patch",),
        ).fetchone()[0])
        self.assertEqual(result["outcome"], "error")
        self.assertEqual(result["code"], "invalid_arguments")
        self.assertFalse(result["sideEffectsMayExist"])
        self.assertFalse(result["reconciliationRequired"])
        self.assertEqual(
            self.store.connection.execute(
                "SELECT COUNT(*) FROM durable_intents WHERE run_id = ?", (run["id"],)
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.store.connection.execute(
                "SELECT COUNT(*) FROM approvals WHERE run_id = ?", (run["id"],)
            ).fetchone()[0],
            0,
        )
        snapshot = self.store.read_session_snapshot(self.session["id"])
        assistant_text = [
            item.get("content")
            for item in snapshot["items"]
            if item["kind"] == "assistant_message"
        ]
        self.assertEqual(
            assistant_text,
            ["I will write the file now.", "Recovered after receiving the tool error."],
        )

    def test_valid_and_invalid_read_calls_keep_batch_order(self) -> None:
        (self.workspace / "hello.txt").write_text("hello\n", encoding="utf-8")
        run, _ = self.store.create_run(self.session["id"], "Read with one invalid argument")
        model = ScriptedModel([
            ModelResponse(
                tool_calls=(
                    ModelToolCall("valid-read", "read_file", {"path": "hello.txt"}),
                    ModelToolCall("invalid-read", "read_file", {"path": 123}),
                ),
            ),
            ModelResponse(
                text="Recovered from the invalid read argument.",
                phase=AssistantMessagePhase.FINAL_ANSWER,
            ),
        ])

        RuntimeLoop(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )

        self.assertEqual(self.store.read_run(run["id"])["status"], "succeeded")
        attempts = self.store.read_model_attempts(run["id"])
        self.assertEqual([attempt["status"] for attempt in attempts], ["completed", "completed"])
        assert self.store.connection is not None
        rows = self.store.connection.execute(
            """
            SELECT provider_call_id, result_json
            FROM tool_calls
            JOIN items ON items.id = tool_calls.item_id
            WHERE items.run_id = ?
            ORDER BY tool_calls.batch_order
            """,
            (run["id"],),
        ).fetchall()
        self.assertEqual([row[0] for row in rows], ["valid-read", "invalid-read"])
        self.assertEqual(json.loads(rows[0][1])["outcome"], "success")
        self.assertEqual(json.loads(rows[1][1])["code"], "invalid_arguments")
        self.assertFalse(
            any(
                item.get("type") == "protocol_error"
                for context in model.contexts
                for item in context
            )
        )

    def test_absolute_outside_path_is_a_tool_error_and_model_recovers(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Recover from an outside read")
        invalid_path = "/not/authorized"
        model = ScriptedModel([
            ModelResponse(
                text="I will inspect the requested directory.",
                phase=AssistantMessagePhase.COMMENTARY,
                tool_calls=(
                    ModelToolCall(
                        "outside-list",
                        "list_files",
                        {"path": invalid_path},
                    ),
                ),
            ),
            ModelResponse(
                text="I will inspect the workspace instead.",
                phase=AssistantMessagePhase.COMMENTARY,
                tool_calls=(
                    ModelToolCall("workspace-list", "list_files", {"path": "."}),
                ),
            ),
            ModelResponse(
                text="Recovered with the correct resource boundary.",
                phase=AssistantMessagePhase.FINAL_ANSWER,
            ),
        ])

        RuntimeLoop(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )

        completed = self.store.read_run(run["id"])
        self.assertEqual(completed["status"], "succeeded")
        self.assertIsNone(completed.get("errorCode"))
        self.assertEqual(completed["modelStepCount"], 3)
        attempts = self.store.read_model_attempts(run["id"])
        self.assertEqual(
            [attempt["status"] for attempt in attempts],
            ["completed", "completed", "completed"],
        )
        self.assertTrue(all(attempt["errorCode"] is None for attempt in attempts))
        self.assertFalse(
            any(
                item.get("type") == "protocol_error"
                for context in model.contexts
                for item in context
            )
        )
        assert self.store.connection is not None
        outside_result = json.loads(self.store.connection.execute(
            "SELECT result_json FROM tool_calls WHERE provider_call_id = ?",
            ("outside-list",),
        ).fetchone()[0])
        self.assertEqual(outside_result["code"], "path_outside_authorized_roots")
        self.assertIn("authorized workspace", outside_result["summary"])
        self.assertFalse(outside_result["sideEffectsMayExist"])
        self.assertFalse(outside_result["reconciliationRequired"])
        self.assertEqual(
            self.store.connection.execute(
                "SELECT COUNT(*) FROM durable_intents WHERE run_id = ?", (run["id"],)
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.store.connection.execute(
                "SELECT COUNT(*) FROM approvals WHERE run_id = ?", (run["id"],)
            ).fetchone()[0],
            0,
        )

    def test_protocol_failure_persists_safe_tool_diagnostics(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Record bad tool details")
        model = ScriptedModel([
            ModelResponse(
                text="I will write the file now.",
                phase=AssistantMessagePhase.COMMENTARY,
                finish_reason="tool_call",
                provider_name="volcengine",
                resolved_model_name="deepseek-v4-flash-ga-260731",
                provider_response_id="provider-response-1",
                response_state="complete",
                tool_calls=(
                    ModelToolCall(
                        "bad-patch",
                        "apply_patch",
                        CustomToolPayload(input="sk-do-not-persist"),
                    ),
                ),
            ),
            ModelResponse(
                text="Recovered with a valid response.",
                phase=AssistantMessagePhase.FINAL_ANSWER,
            ),
        ])

        RuntimeLoop(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )

        attempt = self.store.read_model_attempts(run["id"])[0]
        diagnostic = attempt["protocolDiagnostics"]
        assert isinstance(diagnostic, dict)
        self.assertEqual(attempt["responseState"], "complete")
        self.assertEqual(attempt["phase"], "commentary")
        self.assertEqual(attempt["configuredProviderId"], "deepseek")
        self.assertEqual(attempt["providerName"], "volcengine")
        self.assertEqual(attempt["toolCallCount"], 1)
        self.assertEqual(
            attempt["responseTextSha256"],
            hashlib.sha256("I will write the file now.".encode()).hexdigest(),
        )
        self.assertEqual(
            attempt["responseTextBytes"],
            len("I will write the file now.".encode()),
        )
        self.assertEqual(diagnostic["stage"], "tool_validation")
        self.assertEqual(diagnostic["toolCallIndex"], 0)
        self.assertEqual(diagnostic["toolName"], "apply_patch")
        self.assertEqual(diagnostic["providerCallId"], "bad-patch")
        self.assertEqual(diagnostic["payloadKind"], "custom")
        self.assertTrue(diagnostic["toolDeclared"])
        self.assertEqual(diagnostic["validationCode"], "tool_input_kind_mismatch")
        self.assertEqual(len(diagnostic["toolSetHash"]), 64)
        self.assertEqual(len(diagnostic["contractFingerprint"]), 64)
        self.assertNotIn("inputSha256", diagnostic)
        encoded = json.dumps(diagnostic, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("sk-do-not-persist", encoded)
        database_bytes = b"".join(
            path.read_bytes()
            for path in (
                self.data / "state.sqlite",
                self.data / "state.sqlite-wal",
            )
            if path.exists()
        )
        self.assertNotIn(b"sk-do-not-persist", database_bytes)

    def test_tool_call_response_is_commentary_before_run_completion(
        self,
    ) -> None:
        (self.workspace / "sample.py").write_text("value = 1\n", encoding="utf-8")
        run, _ = self.store.create_run(
            self.session["id"], "Read sample.py and report its current value"
        )
        model = ScriptedModel([
            ModelResponse(
                text="I will read the file now.",
                phase=AssistantMessagePhase.COMMENTARY,
                finish_reason="tool_calls",
                tool_calls=(
                    ModelToolCall("read-sample", "read_file", {"path": "sample.py"}),
                ),
            ),
            ModelResponse(
                text="sample.py currently sets value to 1.",
                phase=AssistantMessagePhase.FINAL_ANSWER,
                finish_reason="stop",
            ),
        ])

        RuntimeLoop(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )

        completed = self.store.read_run(run["id"])
        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(completed["modelStepCount"], 2)
        attempts = self.store.read_model_attempts(run["id"])
        self.assertEqual(
            [attempt["status"] for attempt in attempts],
            ["completed", "completed"],
        )
        snapshot = self.store.read_session_snapshot(self.session["id"])
        self.assertEqual(
            [item["kind"] for item in snapshot["items"]],
            ["user_message", "assistant_message", "tool_call", "assistant_message"],
        )
        self.assertEqual(
            [
                item.get("content")
                for item in snapshot["items"]
                if item["kind"] == "assistant_message"
            ],
            ["I will read the file now.", "sample.py currently sets value to 1."],
        )

    def test_final_response_marker_is_plain_text(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Answer directly")
        text = "This is the final answer.\n<!-- eidos-final-response -->"
        model = ScriptedModel([ModelResponse(
            text=text,
            phase=AssistantMessagePhase.FINAL_ANSWER,
            provider_name="deepseek",
            finish_reason="stop",
        )])

        RuntimeLoop(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )

        completed = self.store.read_run(run["id"])
        self.assertEqual(completed["status"], "succeeded")
        snapshot = self.store.read_session_snapshot(self.session["id"])
        self.assertEqual(
            [item.get("content") for item in snapshot["items"]],
            ["Answer directly", text],
        )
        self.assertIn("eidos-final-response", str(snapshot))

    def test_normal_stop_answer_completes_run_without_undeclared_response(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Answer directly")
        model = ScriptedModel([ModelResponse(
            text="This is the final answer.",
            phase=AssistantMessagePhase.FINAL_ANSWER,
            finish_reason="stop",
        )])

        RuntimeLoop(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )

        completed = self.store.read_run(run["id"])
        self.assertEqual(completed["status"], "succeeded")
        attempts = self.store.read_model_attempts(run["id"])
        self.assertEqual(attempts[0]["status"], "completed")
        self.assertIsNone(attempts[0]["errorCode"])

    def test_unknown_final_response_completes_the_run(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Inspect before answering")
        undeclared = ModelResponse(
            text="Let me inspect the workspace first.",
            phase=None,
        )
        model = ScriptedModel([undeclared, undeclared])

        RuntimeLoop(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )

        completed = self.store.read_run(run["id"])
        self.assertEqual(completed["status"], "succeeded")
        self.assertIsNone(completed.get("errorCode"))
        attempts = self.store.read_model_attempts(run["id"])
        self.assertEqual([attempt["status"] for attempt in attempts], ["completed"])
        self.assertIsNone(attempts[0]["errorCode"])
        snapshot = self.store.read_session_snapshot(self.session["id"])
        self.assertEqual(
            [item["kind"] for item in snapshot["items"]],
            ["user_message", "assistant_message"],
        )

    def test_provider_control_text_without_structured_call_is_repaired(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Repair provider control output")
        model = ScriptedModel([
            ModelResponse(
                text=(
                    "<|DSML|tool_calls><|DSML|invoke name=\"write_file\">"
                    "<|DSML|parameter name=\"path\">go.mod"
                )
            ),
            ModelResponse(
                text="Recovered from provider protocol output.",
                phase=AssistantMessagePhase.FINAL_ANSWER,
            ),
        ])

        RuntimeLoop(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )

        completed = self.store.read_run(run["id"])
        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(completed["modelStepCount"], 1)
        attempts = self.store.read_model_attempts(run["id"])
        self.assertEqual([attempt["status"] for attempt in attempts], ["failed", "completed"])
        self.assertEqual(attempts[0]["errorCode"], "provider_control_syntax")
        snapshot = self.store.read_session_snapshot(self.session["id"])
        assistant_text = [
            item.get("content")
            for item in snapshot["items"]
            if item["kind"] == "assistant_message"
        ]
        self.assertEqual(assistant_text, ["Recovered from provider protocol output."])
        self.assertFalse(any("DSML" in str(value) for value in assistant_text))

    def test_repeated_protocol_failure_is_a_failed_run_not_loop_stop(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Keep returning bad tools")
        bad = ModelResponse(tool_calls=(
            ModelToolCall("bad-write", "write_file", {"path": "go.mod"}),
        ))
        model = ScriptedModel([bad, bad])

        RuntimeLoop(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )

        failed = self.store.read_run(run["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["errorCode"], "MODEL_PROTOCOL_ERROR")
        self.assertIsNone(failed.get("stopReason"))
        self.assertEqual(failed["modelStepCount"], 1)
        attempts = self.store.read_model_attempts(run["id"])
        self.assertEqual([attempt["status"] for attempt in attempts], ["failed", "failed"])
        diagnostic = attempts[0]["protocolDiagnostics"]
        assert isinstance(diagnostic, dict)
        self.assertNotIn("argumentsSha256", diagnostic)

    def test_argument_summary_keeps_shape_without_parameter_digest(self) -> None:
        argument_bytes, keys, types, truncated = argument_summary({
            "a" * 257: "secret",
            "visible": 1,
        })

        self.assertIsNotNone(argument_bytes)
        self.assertEqual(keys, ("<truncated>", "visible"))
        self.assertEqual(types, {"<truncated>": "string", "visible": "integer"})
        self.assertFalse(truncated)

    def test_protocol_diagnostic_rejects_removed_custom_input_digest(self) -> None:
        from pydantic import ValidationError

        from eidos_runtime.runtime.protocol_diagnostics import (
            ProtocolDiagnostic,
            serialize_protocol_diagnostic,
        )

        diagnostic = ProtocolDiagnostic(
            stage="tool_validation",
            code="tool_input_kind_mismatch",
            payload_kind="custom",
            input_bytes=3,
        )
        serialized = json.loads(serialize_protocol_diagnostic(diagnostic))

        self.assertNotIn("input_sha256", ProtocolDiagnostic.model_fields)
        self.assertNotIn("inputSha256", serialized)
        with self.assertRaises(ValidationError):
            ProtocolDiagnostic.model_validate({
                **serialized,
                "inputSha256": "a" * 64,
            })


if __name__ == "__main__":
    unittest.main()
