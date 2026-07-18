from __future__ import annotations

import json

from eidos_runtime.sensitive import SensitiveScanError, SensitiveScanner
from eidos_runtime.tools import canonical_tool_result


def tool_error(tool_name: str, code: str, summary: str) -> dict[str, object]:
    return {
        "schemaVersion": 1, "toolContractVersion": 1, "toolName": tool_name,
        "outcome": "error", "code": code, "summary": summary, "data": {},
        "sideEffectsMayExist": False, "reconciliationRequired": False,
    }


def bounded_tool_result(tool_name: str, result: dict[str, object]) -> dict[str, object]:
    result = canonical_tool_result(tool_name, result)
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(encoded) <= 512 * 1024:
        return result
    return tool_error(tool_name, "tool_result_too_large", "Tool result exceeded the safe size limit")


def safe_tool_result(
    scanner: SensitiveScanner, tool_name: str, result: dict[str, object]
) -> dict[str, object]:
    try:
        scanned = scanner.scan_json(result)
    except SensitiveScanError:
        return tool_error(tool_name, "sensitive_content_rejected", "Tool output was withheld")
    assert isinstance(scanned, dict)
    if scanned != result:
        return tool_error(tool_name, "sensitive_content_rejected", "Tool output was withheld")
    return canonical_tool_result(tool_name, scanned)
