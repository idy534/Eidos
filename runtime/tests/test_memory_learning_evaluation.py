import json
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

import pytest

from eidos_runtime.model.client import FunctionToolDefinition, ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.model.config import ModelConfigStore, default_profile_snapshot
from eidos_runtime.model.gateway import ModelGateway


pytestmark = pytest.mark.integration


def test_evaluation_writes_requested_report_without_touching_model_config(tmp_path, monkeypatch):
    config_directory = tmp_path / "read-only-model-config"
    destination = tmp_path / "requested-report.json"
    model = ScriptedModel([])
    model.profile_snapshot = default_profile_snapshot("deepseek-flash")
    config = SimpleNamespace(id="deepseek-flash", supports_tool_call=True, reasoning=None)
    monkeypatch.setattr(ModelConfigStore, "get", lambda self, identifier: config)
    monkeypatch.setattr(ModelGateway, "acquire_lease", lambda *args, **kwargs:
        SimpleNamespace(client=model, close=lambda: None))
    script = Path(__file__).resolve().parents[2] / "scripts" / "evaluate-memory-learning.py"
    monkeypatch.setattr(sys, "argv", [str(script), "--data-dir", str(config_directory), "--output", str(destination)])
    namespace = runpy.run_path(str(script), run_name="__main__")
    report = json.loads(destination.read_text())
    assert report["model_id"] == "deepseek-flash"
    assert report["requests"] <= 36
    assert len(report["results"]) == 15
    assert report["review_required"]
    assert not config_directory.exists()
    import threading

    model.responses = [ModelResponse(tool_calls=(ModelToolCall("assessment", "submit_memory_fact_assessment", {}),))]
    model._index = 0
    definition = FunctionToolDefinition(name="submit_memory_fact_assessment", description="Fact output", parameters_json_schema={})
    namespace["EvalModel"]().complete((), threading.Event(), lambda text: None, tool_definitions=(definition,), instructions="Fact review")
    assert model.tool_definitions_history[-1] == (definition,)
