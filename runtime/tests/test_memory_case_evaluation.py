import json
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

import pytest

from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.model.config import ModelConfigStore, default_profile_snapshot
from eidos_runtime.model.gateway import ModelGateway


pytestmark = pytest.mark.integration
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "evaluate-memory-cases.py"


def run_script(tmp_path, monkeypatch, responses, *arguments):
    config_directory = tmp_path / "read-only-model-config"
    destination = tmp_path / "report.json"
    model = ScriptedModel(responses)
    model.profile_snapshot = default_profile_snapshot("deepseek-flash")
    config = SimpleNamespace(id="deepseek-flash", supports_tool_call=True, reasoning=None)
    monkeypatch.setattr(ModelConfigStore, "get", lambda self, identifier: config)
    monkeypatch.setattr(ModelGateway, "acquire_lease", lambda *args, **kwargs:
        SimpleNamespace(client=model, close=lambda: None))
    monkeypatch.setattr(sys, "argv", [
        str(SCRIPT), "--data-dir", str(config_directory), "--output", str(destination), *arguments,
    ])
    runpy.run_path(str(SCRIPT), run_name="__main__")
    assert not config_directory.exists()
    return json.loads(destination.read_text())


def test_case_report_judges_recall_from_state_and_answer(tmp_path, monkeypatch):
    report = run_script(
        tmp_path,
        monkeypatch,
        [
            ModelResponse(tool_calls=(ModelToolCall("read", "memory_read", {"path": "deploy.md"}),)),
            ModelResponse(text="./scripts/deploy.sh --env=staging"),
        ],
        "--case", "recall_visible", "--repeats", "1",
    )
    assert report["model_id"] == "deepseek-flash"
    [record] = report["results"]
    assert record["case"] == "recall_visible" and record["group"] == "R"
    assert record["passed"] is True
    assert record["memory_tool_calls"] == 1
    assert report["summary"]["recall_visible"] == {"passed": 1, "attempts": 1}
    assert report["summary"]["group:R"] == {"passed": 1, "attempts": 1}
    assert report["review_required"]


def test_case_report_records_failed_checks_and_request_limit(tmp_path, monkeypatch):
    looping = [
        ModelResponse(tool_calls=(ModelToolCall(f"search-{index}", "memory_search", {}),))
        for index in range(4)
    ]
    report = run_script(
        tmp_path, monkeypatch, looping,
        "--case", "instruction_in_file", "--repeats", "1", "--max-requests", "2",
    )
    [record] = report["results"]
    assert record["requests"] == 2
    assert record["checks"] == [{"type": "no_new_notes", "passed": True}]
    assert record["run_statuses"] != ["succeeded"]


def test_every_fixture_case_uses_known_groups_and_checks():
    fixture = json.loads((SCRIPT.parent / "fixtures" / "memory-cases.json").read_text())
    known = {
        "run_succeeded", "note_contains", "note_count", "note_revised", "no_new_notes",
        "answer_contains", "answer_not_contains", "tool_called", "index_hides", "note_length_at_least",
    }
    names = [case["name"] for case in fixture["cases"]]
    assert len(names) == len(set(names))
    assert {case["group"] for case in fixture["cases"]} == {"C", "P", "U", "R", "T", "I", "X", "E"}
    for case in fixture["cases"]:
        assert case["turns"] and case["checks"]
        assert {rule["type"] for rule in case["checks"]} <= known
        assert case.get("tools", "memory") in {"memory", "all"}
