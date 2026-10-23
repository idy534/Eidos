from __future__ import annotations

import json
import threading

import pytest
from pydantic import ValidationError

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.extensions.plugins import PluginCatalog
from eidos_runtime.extensions.skill_manifest import skill_document
from eidos_runtime.extensions.skills import SkillCatalog, SkillReadError
from eidos_runtime.file_limits import (
    MAX_SKILL_ARGUMENT_BYTES,
    MAX_SKILL_FILE_BYTES,
    MAX_SKILL_FILES,
    MAX_SKILL_TOTAL_BYTES,
    function_argument_limit,
)
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.runtime.approval import ApprovalDecision
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.tools.contracts import SkillCreateInput, SkillReadResourceInput


def create_arguments(**updates):
    return {
        "name": "complete-skill",
        "description": "Use the complete resources.",
        "instructions": "Read the references.",
        **updates,
    }


def create_entry(store):
    catalog = SkillCatalog(PluginCatalog(store))
    return next(entry for entry in catalog.tool_entries(catalog.extension_snapshot())
                if entry.spec.name == "skill_create")


def test_complete_tree_budget_includes_json_escaping_and_generated_document(tmp_path):
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        # Both files fit the persisted tree exactly. Their JSON strings require
        # six bytes per content byte; the old generic argument cap was 64 KiB.
        arguments = create_arguments()
        overhead = len(skill_document(arguments["name"], arguments["description"], ""))
        arguments["instructions"] = "\x01" * (MAX_SKILL_FILE_BYTES - overhead - 1)
        arguments["files"] = [{"path": "references/all.txt", "content": "\x01" * MAX_SKILL_FILE_BYTES}]
        encoded = json.dumps(arguments, ensure_ascii=False, separators=(",", ":")).encode()
        assert len(encoded) > MAX_SKILL_TOTAL_BYTES
        assert len(encoded) < MAX_SKILL_ARGUMENT_BYTES
        validation = create_entry(store).validate_arguments(arguments)
        assert validation.valid, validation
        parsed = SkillCreateInput.model_validate_json(json.dumps(arguments))
        document = skill_document(parsed.name, parsed.description, parsed.instructions)
        assert len(document) == MAX_SKILL_FILE_BYTES
        assert len(document) + len(parsed.files[0].content.encode()) == MAX_SKILL_TOTAL_BYTES
        arguments["instructions"] += "x"
        with pytest.raises(ValidationError, match="skill_instructions_too_large"):
            SkillCreateInput.model_validate_json(json.dumps(arguments))
    finally:
        store.close()
    assert function_argument_limit("skill_create") == MAX_SKILL_ARGUMENT_BYTES
    assert function_argument_limit("read_file") == 64 * 1024


def test_tree_size_and_file_count_match_catalog_boundaries():
    resources = [{"path": f"references/{index}.txt", "content": "text"}
                 for index in range(MAX_SKILL_FILES - 1)]
    SkillCreateInput.model_validate_json(json.dumps(create_arguments(files=resources)))
    with pytest.raises(ValidationError):
        SkillCreateInput.model_validate_json(json.dumps(create_arguments(
            files=[*resources, {"path": "one-more.txt", "content": "text"}],
        )))
    for contents in ("x" * (MAX_SKILL_FILE_BYTES + 1), "中" * (MAX_SKILL_FILE_BYTES // 3 + 1)):
        with pytest.raises(ValidationError):
            SkillCreateInput.model_validate_json(json.dumps(create_arguments(
                files=[{"path": "too-large.txt", "content": contents}],
            )))
    with pytest.raises(ValidationError, match="skill_too_large"):
        SkillCreateInput.model_validate_json(json.dumps(create_arguments(files=[
            {"path": "one.txt", "content": "x" * MAX_SKILL_FILE_BYTES},
            {"path": "two.txt", "content": "x" * MAX_SKILL_FILE_BYTES},
        ])))


@pytest.mark.parametrize("decision", ["approve", "reject", "cancel"])
@pytest.mark.integration
def test_large_create_preserves_approval_intent_cancel_and_restart(tmp_path, decision):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data = tmp_path / "data"
    store = SessionStore(data)
    store.initialize()
    instructions = "Read all resources.\n" + "a" * (600 * 1024)
    resource = "中" * (400 * 1024)
    arguments = create_arguments(instructions=instructions, files=[
        {"path": "references/large.txt", "content": resource},
        *[{"path": f"references/{index}.txt", "content": "Supporting text."}
          for index in range(70)],
    ])
    cancel = threading.Event()
    requests = []

    def approve(request, _cancel):
        requests.append(request)
        if decision == "cancel":
            cancel.set()
        return ApprovalDecision("reject" if decision == "reject" else "approve")

    try:
        catalog = SkillCatalog(PluginCatalog(store))
        old_snapshot = catalog.extension_snapshot()
        session = store.create_session(str(workspace))
        run, _ = store.create_run(session["id"], "Create the complete skill", extension_snapshot=old_snapshot)
        model = ScriptedModel([
            ModelResponse(tool_calls=(ModelToolCall("create", "skill_create", arguments),)),
            ModelResponse(text="Done."),
        ])
        RuntimeEngine(store, model, lambda _message: None, request_approval=approve).run(run["id"], cancel)
        assert len(requests) == 1
        assert requests[0]["kind"] == "file_change"
        destination = data / "skills" / "complete-skill"
        assert destination.exists() == (decision == "approve")
        if decision == "approve":
            assert (destination / "SKILL.md").read_bytes() == skill_document(
                arguments["name"], arguments["description"], instructions + "\n",
            )
            assert (destination / "references/large.txt").read_text() == resource
            intent = store.connection.execute(
                "SELECT status FROM durable_intents WHERE run_id=?", (run["id"],),
            ).fetchone()
            assert intent is not None and intent[0] == "completed"
            with pytest.raises(SkillReadError):
                catalog.read_skill(old_snapshot, "user:complete-skill")
        assert store.read_run(run["id"])["status"] == ("canceled" if decision == "cancel" else "succeeded")
    finally:
        store.close()

    # A new Runtime must discover and read everything that creation accepted.
    store = SessionStore(data)
    store.initialize()
    try:
        catalog = SkillCatalog(PluginCatalog(store))
        if decision == "approve":
            snapshot = catalog.extension_snapshot()
            assert instructions in catalog.read_skill(snapshot, "user:complete-skill")["content"]
            assert catalog.read_resource(snapshot, "user:complete-skill", "references/large.txt")["content"] == resource
            entry = next(entry for entry in catalog.tool_entries(snapshot) if entry.spec.name == "skill_read_resource")
            offset = 1100 * 1024
            offset -= offset % 3
            page = SkillReadResourceInput(
                qualifiedId="user:complete-skill", resourcePath="references/large.txt", offset=offset,
            )
            result = entry.adapter.execute(page.model_dump(), threading.Event())
            assert result["outcome"] == "success"
            assert result["data"]["startByte"] == offset
            assert result["data"]["content"] == "中" * 8000
        else:
            assert not (data / "skills" / "complete-skill").exists()
    finally:
        store.close()
