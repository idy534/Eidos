from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

from pydantic import ValidationError


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.tools.contracts import (  # noqa: E402
    ApplyPatchInput,
    ReadFileInput,
    ReadFileRangeInput,
    ReadFileRangeResultData,
    RunShellInput,
    SearchTextInput,
    WorkspaceResultData,
    WriteFileInput,
    project_tool_result,
    result_model,
)
from eidos_runtime.db.storage import SessionStore  # noqa: E402


def success(tool_name: str, data: dict[str, object]) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "toolContractVersion": 1,
        "toolName": tool_name,
        "outcome": "success",
        "code": "ok",
        "summary": "ok",
        "data": data,
        "sideEffectsMayExist": False,
        "reconciliationRequired": False,
    }


class Phase4B1ContractTests(unittest.TestCase):
    def test_input_models_own_byte_range_newline_and_path_contracts(self) -> None:
        for model, value in (
            (ReadFileInput, {"path": "../secret"}),
            (ReadFileInput, {"path": "/tmp/secret"}),
            (ReadFileRangeInput, {"path": "a", "startLine": 2, "endLine": 1}),
            (ReadFileRangeInput, {"path": "a", "startLine": 1, "endLine": 2001}),
            (SearchTextInput, {"query": "a\nb"}),
            (SearchTextInput, {"query": "界" * 171}),
            (WriteFileInput, {"path": "a", "content": "界" * 87_382}),
            (ApplyPatchInput, {"path": "a", "patch": "界" * 174_763}),
            (RunShellInput, {"command": "界" * 5_462}),
        ):
            with self.subTest(model=model.__name__):
                with self.assertRaises(ValidationError):
                    model.model_validate(value)

    def test_success_result_semantics_are_structural(self) -> None:
        range_model = result_model(ReadFileRangeResultData)
        with self.assertRaises(ValidationError):
            range_model.model_validate(success("read_file_range", {
                "path": "a",
                "content": "",
                "sizeBytes": 0,
                "sha256": "A" * 64,
                "startLine": 1,
                "endLine": 0,
            }))
        with self.assertRaises(ValidationError):
            range_model.model_validate(success("read_file_range", {
                "path": "a",
                "content": "",
                "sizeBytes": 0,
                "sha256": "a" * 64,
                "startLine": 2,
                "endLine": 3,
                "nextLine": 3,
            }))

        workspace_model = result_model(WorkspaceResultData)
        with self.assertRaises(ValidationError):
            workspace_model.model_validate(success("write_file", {
                "path": "a",
                "sizeBytes": -1,
            }))

    def test_reconciliation_requires_possible_side_effects(self) -> None:
        model = result_model(WorkspaceResultData)
        value = success("write_file", {"path": "a"})
        value.update({
            "outcome": "error",
            "sideEffectsMayExist": False,
            "reconciliationRequired": True,
        })
        with self.assertRaises(ValidationError):
            model.model_validate(value)

    def test_projection_enforces_serialized_budget_and_shape_limits(self) -> None:
        adversarial = {
            f"k{index:04d}" + ("界" * 100): {}
            for index in range(2_000)
        }
        adversarial["numbers"] = list(range(20_000))
        cursor: dict[str, object] = adversarial
        for index in range(30):
            child: dict[str, object] = {}
            cursor[f"nested{index}"] = child
            cursor = child
        projected = project_tool_result(
            "mcp__demo__large",
            success("mcp__demo__large", {
                "isError": False,
                "structuredContent": adversarial,
            }),
        )
        encoded = json.dumps(
            projected.model_result,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        self.assertLessEqual(len(encoded), 48 * 1024)
        self.assertTrue(projected.model_result["data"]["truncated"])

    def test_fingerprint_preserves_order_except_declared_path_sets(self) -> None:
        search_a = success("search_text", {
            "matches": [
                {"path": "a", "line": 1, "column": 1, "preview": "first"},
                {"path": "b", "line": 1, "column": 1, "preview": "second"},
            ],
            "scannedBytes": 2,
            "truncated": False,
        })
        search_b = {
            **search_a,
            "data": {**search_a["data"], "matches": list(reversed(search_a["data"]["matches"]))},
        }
        self.assertNotEqual(
            project_tool_result("search_text", search_a).progress_fingerprint,
            project_tool_result("search_text", search_b).progress_fingerprint,
        )

        shell_a = success("run_shell", {
            "exitCode": 0,
            "stdout": "",
            "stderr": "",
            "truncated": False,
            "termination": "exit",
            "workspaceChanged": True,
            "modified": ["b", "a"],
            "durationMs": 1,
        })
        shell_b = {
            **shell_a,
            "data": {
                **shell_a["data"],
                "modified": ["a", "b"],
                "durationMs": 99,
            },
        }
        self.assertEqual(
            project_tool_result("run_shell", shell_a).progress_fingerprint,
            project_tool_result("run_shell", shell_b).progress_fingerprint,
        )

    def test_persisted_ui_projection_matches_live_item_after_reload(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-ui-projection-") as root:
            data = Path(root) / "data"
            workspace = Path(root) / "workspace"
            data.mkdir(mode=0o700)
            workspace.mkdir()
            store = SessionStore(data)
            store.initialize()
            session = store.create_session(str(workspace))
            run, _ = store.create_run(session["id"], "read")
            store.increment_model_step(run["id"])
            item = store.create_tool_item(
                run["id"], 1, 0, "call", "list_files", "{}"
            )
            canonical = success(
                "list_files", {"paths": ["b", "a"], "truncated": False}
            )
            projection = project_tool_result("list_files", canonical)
            mutation = store.complete_tool_item_once_committed(
                item["id"],
                json.dumps(canonical, separators=(",", ":"), sort_keys=True),
                model_result_json=json.dumps(
                    projection.model_result,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                ui_result_json=json.dumps(
                    projection.ui_result,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                progress_fingerprint=projection.progress_fingerprint,
                item_status="completed",
                tool_status="completed",
            )
            live = mutation.value["toolCall"]["resultJson"]
            store.close()

            reopened = SessionStore(data)
            reopened.initialize()
            reloaded = reopened.read_item(item["id"])
            self.assertEqual(live, reloaded["toolCall"]["resultJson"])
            reopened.close()


if __name__ == "__main__":
    unittest.main()
