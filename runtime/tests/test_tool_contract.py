from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.tools.workspace import TOOL_SPECS, canonical_tool_result  # noqa: E402


class ToolContractTests(unittest.TestCase):
    def test_registry_and_cross_language_vectors_are_canonical(self) -> None:
        self.assertEqual(len({spec.name for spec in TOOL_SPECS}), len(TOOL_SPECS))
        fixture = json.loads(
            (RUNTIME_ROOT.parent / "protocol" / "fixtures" / "tool-results-v1.json")
            .read_text(encoding="utf-8")
        )
        for vector in fixture["vectors"]:
            result = vector["result"]
            self.assertEqual(canonical_tool_result(result["toolName"], result), result)
            self.assertEqual(
                json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                vector["canonicalJson"],
            )

    def test_result_rejects_integers_outside_the_json_safe_range(self) -> None:
        with self.assertRaises(ValueError):
            canonical_tool_result("read_file", {
                "outcome": "success", "code": "ok", "summary": "Read file",
                "data": {"sizeBytes": 9_007_199_254_740_992},
                "sideEffectsMayExist": False,
            })


if __name__ == "__main__":
    unittest.main()
