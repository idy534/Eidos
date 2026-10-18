from __future__ import annotations

import json
from pathlib import Path
import threading

import pytest

from eidos_runtime.application.runs import RunApplication
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ModelUsage, ScriptedModel
from eidos_runtime.model.config import default_profile_snapshot
from eidos_runtime.persistence.context_management import ContextManagementRepository
from eidos_runtime.protocol.methods import ContextUsageRequestDto
from eidos_runtime.runtime.engine import RuntimeEngine


def test_breakdown_attributes_subagent_definitions_and_user_images_to_their_sections():
    from eidos_runtime.context.budget import estimate_model_request_budget
    params = dict(instructions="System", tool_definitions=(), context_window_tokens=100000, request_max_output_tokens=1000)
    catalog = estimate_model_request_budget(({
        "type": "user", "sectionId": "agent-catalog", "content": "Agent definition " * 200,
    },), **params)
    assert "conversation" not in {section.name for section in catalog.sections}
    assert next(section.tokens for section in catalog.sections if section.name == "tools") > 200
    image = estimate_model_request_budget(({
        "type": "user", "content": "Inspect image", "inputImage": "fixture", "imageTokenEstimate": 8192,
    },), **params)
    assert next(section.tokens for section in image.sections if section.name == "conversation") > 8000
    assert sum(section.tokens for section in image.sections) == image.estimated_input_tokens


def test_current_total_and_sections_share_provider_usage_and_frozen_reduced_window(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        profile = default_profile_snapshot("deepseek-flash").model_copy(update={"context_window_tokens": 100_000})
        session = store.create_session(str(workspace))
        run, _ = store.create_run(session["id"], "检查上下文", model_profile=profile)
        runtime = RuntimeEngine(store, ScriptedModel([ModelResponse(text="完成", usage=ModelUsage(input_tokens=181_376, output_tokens=2018))]), lambda _: None)
        runtime.run(run["id"], threading.Event())
        reference = store.read_latest_context_budget(run["id"])[1]
        def reject_blob(*_args, **_kwargs):
            raise AssertionError("metrics must not read request bodies")
        monkeypatch.setattr(store.database.json_blobs, "read", reject_blob)
        details = store.read_context_details(run["id"])
        assert details.context_window_tokens == 100_000
        assert details.active_tokens == sum(section.tokens for section in details.sections) == 181_376
        assert details.measurement == "provider"
        assert reference.estimated_input_tokens != details.active_tokens
        assert sum(section.tokens for section in reference.sections) == reference.estimated_input_tokens
        usage = RunApplication(store=store, runtime=runtime).context_usage(ContextUsageRequestDto(runId=run["id"])).context_usage
        assert usage.active_tokens == details.active_tokens
        assert usage.percent_used == 181.4
    finally:
        store.close()


@pytest.mark.parametrize("rebuild_fails", [False, True])
def test_compaction_publishes_current_breakdown_before_the_next_model_request(tmp_path: Path, monkeypatch, rebuild_fails):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        session = store.create_session(str(workspace))
        previous, _ = store.create_run(session["id"], "先查看项目")
        old = store.create_tool_item(previous["id"], 1, 0, "old-files", "list_files", "{}")
        store.complete_tool_item(old["id"], '{"outcome":"success","summary":"Listed files"}')
        store.fail_run(previous["id"], "fixture")
        run, _ = store.create_run(session["id"], "查看文件列表后继续")
        model = ScriptedModel([
            ModelResponse(tool_calls=(ModelToolCall("files", "list_files", {}),), usage=ModelUsage(input_tokens=10_000)),
            ModelResponse(text="完成", usage=ModelUsage(input_tokens=4_000)),
        ])
        complete = model.complete
        if rebuild_fails:
            from eidos_runtime.application.context import ContextApplication
            def unavailable(*_args, **_kwargs):
                raise RuntimeError("fixture projection unavailable")
            monkeypatch.setattr(ContextApplication, "publish_projection", unavailable)
        def request_after_first(*args, **kwargs):
            response = complete(*args, **kwargs)
            if len(model.contexts) == 1:
                ContextManagementRepository(store.database).request(run["id"])
            return response
        monkeypatch.setattr(model, "complete", request_after_first)
        observed = []
        record = ContextManagementRepository.record
        def observe(repository, run_id, value):
            record(repository, run_id, value)
            if value.stage == "finished" and value.status == "succeeded":
                details = store.read_context_details(run_id)
                observed.append(details)
                assert len(model.contexts) == 1
                assert details.active_tokens == value.after_tokens
                assert sum(section.tokens for section in details.sections) == (details.active_tokens or 0)
                assert details.measurement != "provider"
                if rebuild_fails:
                    assert details.measurement == "unavailable"
        monkeypatch.setattr(ContextManagementRepository, "record", observe)
        RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
        assert len(observed) == 1
        assert store.read_run(run["id"])["status"] == "succeeded"
        directory = store.database.data_directory
        store.close()
        restarted = SessionStore(directory)
        restarted.initialize()
        try:
            details = restarted.read_context_details(run["id"])
            assert details.active_tokens == 4_000
            assert sum(section.tokens for section in details.sections) == 4_000
        finally:
            restarted.close()
    finally:
        store.close()


def test_many_uncertain_tool_results_keep_references_without_blocking_later_compaction(tmp_path: Path):
    from eidos_runtime.context.compactor import ContextCompactor
    from eidos_runtime.context.tokens import estimate_text_tokens
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        session = store.create_session(str(workspace))
        run, _ = store.create_run(session["id"], "继续检查")
        originals = {}
        for cycle in range(3):
            for index in range(18):
                item = store.create_tool_item(run["id"], cycle * 20 + index, 0, f"uncertain-{cycle}-{index}", "run_shell", "{}")
                raw = json.dumps({"outcome": "success", "summary": "needs verification " * 100, "data": {"workspaceChangeState": "unknown", "stdout": "details " * 100}})
                store.complete_tool_item(item["id"], raw)
                originals[item["id"]] = raw
            summary = ContextCompactor(store).compact(run["id"], "mid_turn")
            assert any("Unverified tool effects: item=" in value for value in summary.uncertain_side_effects)
            assert estimate_text_tokens(summary.model_dump_json(exclude={"source_item_ids"})) <= 4096
        # The current response window stays verbatim outside the summary.
        from eidos_runtime.context.builder import ContextBuilder
        projected = ContextBuilder(store).build(run["id"]).facts
        assert set(summary.source_item_ids) <= set(originals)
        assert set(originals) <= set(summary.source_item_ids) | {item.item_id for item in projected.items}
        assert all(store.read_item(key)["toolCall"]["resultJson"] == value for key, value in originals.items())
    finally:
        store.close()
