from __future__ import annotations

import unittest
from pathlib import Path
import sys

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.schemas import ApprovalDecisionDto, JsonRpcResponse, RunDto


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


if __name__ == "__main__":
    unittest.main()
