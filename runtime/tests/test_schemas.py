from __future__ import annotations

import unittest
from pathlib import Path
import sys

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.protocol.schemas import (
    ApprovalDecisionDto,
    JsonRpcResponse,
    RunDto,
    ToolResultDataDto,
)


class ClosedSchemaTests(unittest.TestCase):
    def test_unknown_fields_and_coercion_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            ApprovalDecisionDto.model_validate({"decision": "approve", "extra": True})
        with self.assertRaises(ValidationError):
            RunDto.model_validate({
                "id": "run-1", "sessionId": "session-1", "status": "queued",
                "modelStepCount": "0", "createdAt": 1, "updatedAt": 1,
            })

    def test_json_rpc_response_exports_explicit_json(self) -> None:
        value = JsonRpcResponse.model_validate({
            "jsonrpc": "2.0", "id": "client-1", "result": {"ok": True},
        }).to_json_value()
        self.assertEqual(value, {
            "jsonrpc": "2.0", "id": "client-1", "result": {"ok": True},
        })

    def test_run_and_workspace_dependency_result_expose_reconciliation_facts(self) -> None:
        run = RunDto.model_validate({
            "id": "run-1", "sessionId": "session-1", "status": "succeeded",
            "modelId": "model-1", "modelStepCount": 1,
            "createdAt": 1, "updatedAt": 2,
            "reconciliationRequired": True,
        })
        self.assertTrue(run.reconciliation_required)
        self.assertTrue(run.to_json_value()["reconciliationRequired"])

        data = ToolResultDataDto.model_validate({
            "source": "eidos_runtime",
            "pythonPath": ["/app/runtime/.venv/lib/python3.12/site-packages"],
            "pythonPackages": [{
                "name": "python-docx",
                "importName": "docx",
                "version": "1.2.0",
            }],
            "executables": [{
                "name": "python3",
                "path": "/app/runtime/.venv/bin/python",
                "version": "3.12.13",
                "sha256": "a" * 64,
            }],
        })
        self.assertEqual(data.python_path, [
            "/app/runtime/.venv/lib/python3.12/site-packages",
        ])
        self.assertEqual(data.to_json_value()["executables"][0]["name"], "python3")


if __name__ == "__main__":
    unittest.main()
