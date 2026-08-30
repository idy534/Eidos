from __future__ import annotations

from pathlib import Path
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.sandbox.sensitive import (  # noqa: E402
    SensitiveContentDenied,
    SensitiveScanError,
    SensitiveScanner,
    StreamingSensitiveScanner,
)
from eidos_runtime.model.client import ModelResponse, ScriptedModel  # noqa: E402
from eidos_runtime.sandbox.seatbelt import SeatbeltSelfTestResult  # noqa: E402
from eidos_runtime.protocol.server import RuntimeServer  # noqa: E402


class SensitiveScannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scanner = SensitiveScanner()

    def test_deny_redact_and_audit_actions_are_deterministic(self) -> None:
        with self.assertRaises(SensitiveContentDenied) as denied:
            self.scanner.scan_text("key=sk-abcdefghijklmnop")
        self.assertEqual(denied.exception.rule_id, "api_credential")
        redacted = self.scanner.scan_text("password=hunter2 email me@example.com")
        self.assertEqual(
            redacted.text,
            "[REDACTED:secret_assignment] email me@example.com",
        )
        self.assertEqual(redacted.audited_rule_ids, ["email_address"])

    def test_cross_chunk_secret_is_never_released(self) -> None:
        released: list[str] = []
        stream = StreamingSensitiveScanner(self.scanner, released.append)
        stream.feed("credential sk-abcdefgh")
        self.assertEqual(released, [])
        with self.assertRaises(SensitiveContentDenied):
            stream.feed("ijklmnop\n")
        self.assertEqual(released, [])

        redacted: list[str] = []
        assignment = StreamingSensitiveScanner(self.scanner, redacted.append)
        assignment.feed("password:\n")
        self.assertEqual(redacted, [])
        assignment.feed("hunter2\n")
        self.assertEqual(redacted, ["[REDACTED:secret_assignment]\n"])

    def test_invalid_or_writable_rule_resource_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-rules-") as directory:
            rules = Path(directory) / "rules.json"
            rules.write_text("{}", encoding="utf-8")
            with self.assertRaises(SensitiveScanError):
                SensitiveScanner(rules)
            rules.write_text(
                '{"version":1,"rules":[]}', encoding="utf-8"
            )
            rules.chmod(0o666)
            with self.assertRaises(SensitiveScanError):
                SensitiveScanner(rules)

    def test_rejected_run_uses_no_operation_or_persistent_fact(self) -> None:
        with (
            tempfile.TemporaryDirectory(prefix="eidos-sensitive-data-") as data,
            tempfile.TemporaryDirectory(prefix="eidos-sensitive-workspace-") as workspace,
        ):
            output = io.StringIO()
            model = ScriptedModel([ModelResponse(text="unused")])
            server = RuntimeServer(output, Path(data), model)
            with patch(
                "eidos_runtime.protocol.server.run_seatbelt_self_test",
                return_value=SeatbeltSelfTestResult(False, (), ("test",)),
            ):
                server.handle({
                    "jsonrpc": "2.0", "id": "client-init", "method": "initialize",
                    "params": {"client": {"name": "test", "version": "1"}, "protocolVersion": 1},
                })
            session = server.store.create_session(workspace)
            operation_id = "8a4f2ce1-84fe-49d8-8acd-6f73bab2c7d1"
            secret = "sk-abcdefghijklmnop"
            server.handle({
                "jsonrpc": "2.0", "id": "client-run", "method": "run/start",
                "params": {"sessionId": session["id"], "userInput": secret,
                           "modelId": "deepseek-v4-flash",
                           "operationId": operation_id},
            })
            messages = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual(
                messages[-1]["error"]["data"]["code"],
                "SENSITIVE_CONTENT_REJECTED",
            )
            database = Path(data) / "state.sqlite"
            connection = sqlite3.connect(database)
            try:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM operations").fetchone()[0], 0)
            finally:
                connection.close()
            self.assertNotIn(secret.encode(), database.read_bytes())
            self.assertEqual(model.contexts, [])
            server.close()


if __name__ == "__main__":
    unittest.main()
