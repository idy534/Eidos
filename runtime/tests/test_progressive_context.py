from __future__ import annotations

import json
import threading

import pytest

from eidos_runtime.context.builder import ContextBuilder
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.personalization import PersonalizationPreferences
from eidos_runtime.extensions.plugins import PluginCatalog
from eidos_runtime.extensions.skills import SkillCatalog
from eidos_runtime.memory.contracts import MemoryEditRequest, MemoryPolicyRequest
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.model.instructions import StepPermissionPolicy
from eidos_runtime.persistence.personalization import PersonalizationRepository
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.runtime.run_resources import RunResources


pytestmark = pytest.mark.integration

SOURCE = (
    "帮我翻译一下\n\n"
    "Assess only the exact proposed action or permission grant against the user's actual authorization.\n"
    "User messages are authorization evidence."
)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    monkeypatch.setenv("EIDOS_PROJECTLESS_ROOT", str(root))
    workspace = root / "2026-10-31" / "translation"
    workspace.mkdir(parents=True)
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.typed_runtime_repository().create_session(str(workspace), projectless=True).value
    skill = store.data_directory / "skills" / "nebula"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: nebula\ndescription: Build galacticstar presentations.\n---\nUse the user's supplied facts.\n"
    )
    store.database.memory.edit(MemoryEditRequest(
        session_id=session.id, operation_id="seed", path="reference/topic.md",
        summary="Historical reference fixture", content="A saved reference.",
    ))
    yield store, session, workspace
    store.close()


def execute(store, session, request, responses, **run_options):
    run, _ = store.create_run(
        session.id, request, extension_snapshot=SkillCatalog(PluginCatalog(store)).extension_snapshot(), **run_options
    )
    model = ScriptedModel(responses)
    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
    state = store.read_run(run["id"])
    assert state["status"] == "succeeded", state
    return run, model


def tool(name, arguments, call_id=None):
    return ModelResponse(tool_calls=(ModelToolCall(call_id or name, name, arguments),))


def tool_results(store, session):
    return [json.loads(item["toolCall"]["resultJson"])
            for item in store.read_session_snapshot(session.id)["items"] if item.get("toolCall") is not None]


def test_first_request_has_direct_tools_catalog_memory_and_unchanged_user_input(setup):
    store, session, workspace = setup
    run, model = execute(store, session, SOURCE, [ModelResponse(text="译文")])
    names = {value.name for value in model.tool_definitions_history[0]}
    assert {"read_file", "apply_patch", "skill_read", "skill_read_resource", "memory_read"} <= names
    assert "tool_search" not in names
    assert [item["content"] for item in model.contexts[0] if item.get("type") == "user"] == [SOURCE]
    sections = {item.get("sectionId"): item for item in model.contexts[0]}
    assert sections["memory-evidence"]["type"] == "context"
    assert sections["workspace-environment"]["type"] == "context"
    assert str(workspace) in model.instructions_history[0]
    assert "galacticstar" not in model.instructions_history[0]
    assert sections["skill-catalog"]["contextCategory"] == "skills"
    assert "<available_skills>" in sections["skill-catalog"]["content"]
    assert "galacticstar" in sections["skill-catalog"]["content"]
    assert "<load_tool>skill_read</load_tool>" in sections["skill-catalog"]["content"]
    assert sections["memory-evidence"]["contextCategory"] == "memory"
    assert not tool_results(store, session)
    attempt = store.read_model_attempts(run["id"])[0]
    snapshot = store.context_snapshot_repository().read_for_model_attempt(attempt["id"])
    assert snapshot.instructions == model.instructions_history[0]
    assert snapshot.model_context == model.contexts[0]
    assert snapshot.tool_definitions == model.tool_definitions_history[0]


def test_user_preferences_are_separate_source_context(setup):
    store, session, _ = setup
    PersonalizationRepository(store.database).save(PersonalizationPreferences(
        response_language="zh-CN", tone="direct", preferred_name="Eddy", custom_instructions="Use metric units."
    ), 0)
    _, model = execute(store, session, SOURCE, [ModelResponse(text="译文")])
    sections = {item.get("sectionId"): item for item in model.contexts[0]}
    assert "Simplified Chinese" in sections["personalization-expression"]["content"]
    assert "Eddy" in sections["personalization-profile"]["content"]
    assert "Use metric units." in sections["personalization-instructions"]["content"]
    preferences = [value for key, value in sections.items() if str(key).startswith("personalization-")]
    assert all(value["type"] == "context" and value["contextCategory"] == "memory" for value in preferences)
    assert "Use metric units." not in model.instructions_history[0]


def test_long_term_memory_off_keeps_explicit_global_defaults(setup):
    store, session, _ = setup
    PersonalizationRepository(store.database).save(PersonalizationPreferences(
        custom_instructions="Use metric units."
    ), 0)
    store.database.memory.policy(MemoryPolicyRequest(session_id=session.id, scope="global", mode="off"))
    _, model = execute(store, session, "Hello", [ModelResponse(text="Hello")])
    sections = {item.get("sectionId"): item for item in model.contexts[0]}
    assert "memory-evidence" not in sections
    assert sections["personalization-instructions"]["contextCategory"] == "memory"
    assert sections["personalization-instructions"]["memorySource"] == "global_customization"
    assert "Use metric units." in sections["personalization-instructions"]["content"]


def test_invalid_scheduling_timezone_does_not_block_ordinary_context(setup, monkeypatch):
    store, session, _ = setup
    monkeypatch.setenv("TZ", "invalid/scheduling-timezone")
    _, model = execute(store, session, "Hello", [ModelResponse(text="Hello")])
    environment = next(item for item in model.contexts[0] if item.get("sectionId") == "workspace-environment")
    assert "currentDate" in environment["content"]
    assert "timezone" in environment["content"]


def test_known_file_is_read_in_the_first_step_without_activation(setup):
    store, session, workspace = setup
    (workspace / "README.md").write_text("A small example project.\n")
    run, model = execute(store, session, "Read README.md", [
        tool("read_file", {"path": "README.md"}),
        ModelResponse(text="A small example project."),
    ])
    results = tool_results(store, session)
    assert [result["outcome"] for result in results] == ["success"]
    assert "A small example project." in json.dumps(results[0])
    assert store.activated_tools(run["id"]) == ()
    assert any(item.get("type") == "tool_result" for item in model.contexts[1])
    for index, attempt in enumerate(store.read_model_attempts(run["id"])):
        snapshot = store.context_snapshot_repository().read_for_model_attempt(attempt["id"])
        assert snapshot.model_context == model.contexts[index]
        assert snapshot.tool_definitions == model.tool_definitions_history[index]


def test_first_step_write_retains_normal_workspace_permission(setup):
    store, session, workspace = setup
    execute(store, session, "Create result.txt containing created", [
        tool("apply_patch", {"patch": "*** Begin Patch\n*** Add File: result.txt\n+created\n*** End Patch"}),
        ModelResponse(text="Created result.txt"),
    ])
    assert (workspace / "result.txt").read_text() == "created\n"
    assert [result["outcome"] for result in tool_results(store, session)] == ["success"]


def test_cancel_discards_a_late_write_proposal(setup):
    store, session, workspace = setup
    run, _ = store.create_run(session.id, "Create result.txt", extension_snapshot=SkillCatalog(PluginCatalog(store)).extension_snapshot())
    cancel = threading.Event()

    class LateModel(ScriptedModel):
        def complete(self, *args, **kwargs):
            result = super().complete(*args, **kwargs)
            cancel.set()
            return result

    model = LateModel([tool("apply_patch", {"patch": "*** Begin Patch\n*** Add File: result.txt\n+late\n*** End Patch"})])
    RuntimeEngine(store, model, lambda _: None).run(run["id"], cancel)
    assert store.read_run(run["id"])["status"] == "canceled"
    assert not (workspace / "result.txt").exists()
    assert not tool_results(store, session)


def test_skill_is_available_and_its_resources_remain_scoped(setup):
    store, session, _ = setup
    skill = store.data_directory / "skills" / "nebula"
    (skill / "references").mkdir()
    (skill / "references" / "layout.md").write_text("Use a readable layout.")
    _, model = execute(store, session, "Create a galacticstar presentation", [
        tool("skill_read", {"qualifiedId": "user:nebula"}),
        tool("skill_read_resource", {"qualifiedId": "user:nebula", "resourcePath": "references/layout.md"}),
        ModelResponse(text="Read the relevant Skill and resource."),
    ])
    results = tool_results(store, session)
    assert [result["outcome"] for result in results] == ["success", "success"]
    assert "Use the user's supplied facts." in json.dumps(results[0])
    assert "Use a readable layout." in json.dumps(results[1])
    assert "user:nebula" in str(model.contexts[0])


@pytest.mark.parametrize("mode", ["auto", "read", "off"])
def test_initial_memory_context_preserves_read_and_write_modes(setup, mode):
    store, session, _ = setup
    store.database.memory.policy(MemoryPolicyRequest(session_id=session.id, scope="global", mode=mode))
    _, model = execute(store, session, "Hello", [ModelResponse(text="Hello")])
    instructions = model.instructions_history[0]
    names = {value.name for value in model.tool_definitions_history[0]}
    assert ("memory_read" in names) == (mode != "off")
    assert "memory_edit" in names
    assert any(item.get("sectionId") == "memory-evidence" for item in model.contexts[0]) == (mode != "off")
    assert ("Use memory_edit to save reusable" in instructions) == (mode == "auto")


def test_explicit_skill_and_plan_mode_keep_their_required_context(setup):
    store, session, workspace = setup
    _, model = execute(store, session, "Use @user:nebula", [ModelResponse(text="Read the selected instructions.")])
    assert "Use the user's supplied facts." in str(model.contexts[0])
    assert str(workspace) in model.instructions_history[0]
    _, planned = execute(store, session, "Plan the work", [ModelResponse(text="Plan draft")], work_mode="plan")
    assert "The user selected Plan mode" in planned.instructions_history[0]
    assert "write_plan" in {value.name for value in planned.tool_definitions_history[0]}
    assert "read_file" in {value.name for value in planned.tool_definitions_history[0]}


def test_legacy_activation_records_do_not_gate_reopened_resources(setup):
    store, session, _ = setup
    run, _ = store.create_run(session.id, "Read README.md")
    extensions = SkillCatalog(PluginCatalog(store)).extension_snapshot()
    with RunResources(store, run["id"], extensions) as resources:
        fingerprint = resources.registry.get("read_file").contract_fingerprint
        snapshot = resources.registry.snapshot()
        assert "read_file" in snapshot.available_names
        assert snapshot.deferred_names == ()
        assert snapshot.activated_names == ()
        store.activate_tools(run["id"], ("read_file",))
    with RunResources(store, run["id"], extensions) as restored:
        snapshot = restored.registry.snapshot(activated_names=store.activated_tools(run["id"]))
        assert snapshot.binding("read_file").contract_fingerprint == fingerprint
        assert "read_file" in snapshot.available_names
        built = ContextBuilder(store).build(
            run["id"], tool_definitions=restored.registry.model_definitions(),
            retained_context=restored.retained_context,
            step_policy=StepPermissionPolicy(sandbox_mode="workspace-write", workspace_root="/fixture", available_tools=snapshot.available_names),
            projectless=True,
        )
        assert "Workspace root: /fixture" in built.instructions.system_text


def test_project_binding_preserves_direct_workspace_tools(setup, tmp_path):
    store, _, _ = setup
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = store.create_session(str(workspace))
    run, _ = store.create_run(session["id"], "Inspect this project")
    extensions = SkillCatalog(PluginCatalog(store)).extension_snapshot()
    with RunResources(store, run["id"], extensions) as resources:
        assert "read_file" in resources.registry.snapshot().available_names
        assert "tool_search" not in resources.registry.names
