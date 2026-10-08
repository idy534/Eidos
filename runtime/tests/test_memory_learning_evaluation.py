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


@pytest.mark.parametrize("disable_automatic_learning", [False, True])
def test_evaluation_writes_requested_report_without_touching_model_config(tmp_path, monkeypatch, disable_automatic_learning):
    config_directory = tmp_path / "read-only-model-config"
    destination = tmp_path / "requested-report.json"
    model = ScriptedModel([])
    model.profile_snapshot = default_profile_snapshot("deepseek-flash")
    config = SimpleNamespace(id="deepseek-flash", supports_tool_call=True, reasoning=None)
    monkeypatch.setattr(ModelConfigStore, "get", lambda self, identifier: config)
    monkeypatch.setattr(ModelGateway, "acquire_lease", lambda *args, **kwargs:
        SimpleNamespace(client=model, close=lambda: None))
    script = Path(__file__).resolve().parents[2] / "scripts" / "evaluate-memory-learning.py"
    arguments = [str(script), "--data-dir", str(config_directory), "--output", str(destination)]
    if disable_automatic_learning:
        arguments.append("--disable-automatic-learning")
    monkeypatch.setattr(sys, "argv", arguments)
    namespace = runpy.run_path(str(script), run_name="__main__")
    report = json.loads(destination.read_text())
    assert report["model_id"] == "deepseek-flash"
    assert report["requests"] <= 36
    assert len(report["results"]) == 16
    assert report["review_required"]
    assert not config_directory.exists()
    import threading

    model.responses = [ModelResponse(tool_calls=(ModelToolCall("assessment", "_eidos_assess_completion", {}),))]
    model._index = 0
    definition = FunctionToolDefinition(name="_eidos_assess_completion", description="Completion output", parameters_json_schema={})
    namespace["EvalModel"]().complete((), threading.Event(), lambda text: None, tool_definitions=(definition,), instructions="Completion review")
    assert model.tool_definitions_history[-1] == (definition,)

    model.responses = [ModelResponse(tool_calls=(ModelToolCall("assessment", "submit_memory_fact_assessment", {}),))]
    model._index = 0
    obsolete_review = FunctionToolDefinition(name="submit_memory_fact_assessment", description="Fact output", parameters_json_schema={})
    with pytest.raises(RuntimeError, match="eval_unadvertised_tool"):
        namespace["EvalModel"]().complete((), threading.Event(), lambda text: None, tool_definitions=(obsolete_review,), instructions="Fact review")
    assert model.tool_definitions_history[-1] == ()
