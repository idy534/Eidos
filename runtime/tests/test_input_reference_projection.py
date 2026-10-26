from __future__ import annotations

import json
from pathlib import Path
import threading

import pytest

from eidos_runtime.context.builder import ContextBuilder
from eidos_runtime.context.compactor import ContextCompactor
from eidos_runtime.context.facts import CompactNoteEvidence, ContextItemFact
from eidos_runtime.context.input_references import project_input_mentions
from eidos_runtime.context.summary_evidence import ground_note, model_source_text, source_text
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.input_reference import InputReference, InputSnapshot
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.persistence.input_context import InputContextRepository
from eidos_runtime.runtime.engine import RuntimeEngine


def reference(kind: str, *, identifier: str = "a" * 64, label: str = "github") -> InputReference:
    return InputReference(id=identifier, kind=kind, label=label, source=f"{kind}:github",
                          sha256="f" * 64, status="selection", size=1)


@pytest.mark.parametrize("kind,prefix", [
    ("skill", "$"), ("agent", "@"), ("mcp", "@"), ("plugin", "@"), ("history", "@"),
])
@pytest.mark.parametrize("id_length", [8, 12, 64])
def test_bound_mentions_preserve_mixed_text_and_repeated_positions(kind, prefix, id_length):
    selected = reference(kind)
    token = f"[{prefix}github](ref:{selected.id[:id_length]})"
    text = f"先参考{token}，再用{token}检查。🙂"
    assert project_input_mentions(text, (selected,)) == f"先参考{prefix}github，再用{prefix}github检查。🙂"


@pytest.mark.parametrize("kind", ["image", "file", "directory", "excerpt"])
def test_shelf_references_do_not_turn_literal_text_into_mentions(kind):
    selected = reference(kind)
    text = f"[@github](ref:{selected.id[:12]})"
    assert project_input_mentions(text, (selected,)) == text


def test_unknown_short_ambiguous_and_malformed_markers_stay_literal():
    first = reference("skill", identifier="123456789abc" + "a" * 52)
    second = reference("plugin", identifier="123456789abc" + "b" * 52)
    literal = (
        "未知[$x](ref:ffffffffffff) 过短[$x](ref:1234567) "
        "歧义[$x](ref:123456789abc) 普通链接[$x](https://example.com) "
        "换行[$x\ny](ref:123456789abc)"
    )
    assert project_input_mentions(literal, (first, second)) == literal
    assert project_input_mentions(f"[$x](ref:{first.id})", (first, second)) == "$github"
    assert project_input_mentions(f"[$x](ref:{first.id})", (second,)) == f"[$x](ref:{first.id})"


def test_bound_label_and_sigil_come_from_the_saved_reference():
    selected = reference("skill", label=" 报告[技能]\n第二行 ")
    token = f"[@伪造插件](ref:{selected.id[:12]})"
    assert project_input_mentions(token, (selected,)) == "$报告［技能］ 第二行"


@pytest.fixture
def history(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.create_session(str(workspace))
    try:
        yield store, session
    finally:
        store.close()


def save_reference(store: SessionStore, selected: InputReference) -> InputSnapshot:
    snapshot = InputSnapshot(reference=selected, text=f"{selected.kind} saved context",
                             selection_id=selected.source)
    InputContextRepository(store.database).save(snapshot)
    return snapshot


@pytest.mark.parametrize("recovery_level", [0, 1, 2])
def test_builder_projects_all_inline_kinds_and_keeps_durable_inputs(history, recovery_level):
    store, session = history
    references = tuple(reference(kind, identifier=str(index) * 64)
                       for index, kind in enumerate(("skill", "plugin", "mcp", "agent", "history"), 1))
    snapshots = tuple(save_reference(store, selected) for selected in references)
    raw = "先" + "，然后".join(f"[@旧标签](ref:{selected.id[:12]})" for selected in references) + "。"
    repository = InputContextRepository(store.database)
    repository.write_draft(session["id"], raw, [selected.id for selected in references])
    run, user = store.create_run(session["id"], raw, references=list(references))

    built = ContextBuilder(store).build(run["id"], recovery_level=recovery_level)
    assert any(item.get("content") == "先$github，然后@github，然后@github，然后@github，然后@github。"
               for item in built.model_context)
    for selected in references:
        context = next(item for item in built.model_context
                       if item.get("sectionId") == f"input-reference:{selected.id}")
        assert f"类型：{selected.kind}" in context["content"]
        assert f"来源：{selected.source}" in context["content"]
    assert "ref:" not in json.dumps(built.model_context, ensure_ascii=False)
    assert store.read_item(user["id"])["content"] == raw
    assert store.read_run(run["id"])["userInput"] == raw
    assert repository.read_draft(session["id"]).text == raw
    assert tuple(repository.read(selected.id) for selected in references) == snapshots


def test_saved_history_is_projected_after_restart_without_using_other_messages_references(history):
    store, session = history
    selected = reference("history")
    save_reference(store, selected)
    raw = f"请参考[@github](ref:{selected.id[:12]})。"
    previous, user = store.create_run(session["id"], raw, references=[selected])
    store.fail_run(previous["id"], "fixture")
    current, current_user = store.create_run(session["id"], raw)
    data_dir = store.data_directory
    store.close()
    reopened = SessionStore(data_dir)
    reopened.initialize()
    try:
        built = ContextBuilder(reopened).build(current["id"])
        messages = {item.get("sourceItemId"): item.get("content") for item in built.model_context}
        assert messages[user["id"]] == "请参考@github。"
        assert messages[current_user["id"]] == raw
        assert reopened.read_item(user["id"])["content"] == raw
    finally:
        reopened.close()


def test_runtime_sends_projected_user_text_to_model_and_retains_original_item(history):
    store, session = history
    selected = reference("history")
    save_reference(store, selected)
    raw = f"参考[@github](ref:{selected.id[:12]})检查。"
    run, user = store.create_run(session["id"], raw, references=[selected], approval_mode="full_access")
    model = ScriptedModel([ModelResponse(text="Done")])
    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
    assert store.read_run(run["id"])["status"] == "succeeded"
    assert any(item.get("content") == "参考@github检查。" for item in model.contexts[0])
    assert "ref:" not in json.dumps(model.contexts, ensure_ascii=False)
    assert store.read_item(user["id"])["content"] == raw


@pytest.mark.parametrize("assisted", [False, True])
def test_compaction_uses_readable_mentions_and_preserves_original_evidence(history, assisted):
    store, session = history
    selected = reference("skill")
    save_reference(store, selected)
    raw = f"必须使用[$github](ref:{selected.id[:12]})检查。不要提交。"
    clean = "必须使用$github检查。不要提交。"
    previous, user = store.create_run(session["id"], raw, references=[selected])
    store.fail_run(previous["id"], "fixture")
    current, _ = store.create_run(session["id"], raw, references=[selected])

    class SummaryModel:
        profile_snapshot = store.read_model_profile(current["id"])

        def complete(self, context, cancel, on_text_delta, **options):
            payload = json.loads(context[0]["content"])
            assert "ref:" not in json.dumps(payload, ensure_ascii=False)
            assert payload["currentTask"] == clean
            source = next(value for value in payload["sources"] if value["itemId"] == user["id"])
            assert source["body"] == clean
            return ModelResponse(tool_calls=(ModelToolCall("summary", "submit_context_summary", {
                "notes": [{"kind": "decision", "text": clean,
                           "evidence": [{"item_id": user["id"], "quote": clean}]}],
            }),))

    summary = ContextCompactor(store, SummaryModel() if assisted else None).compact(current["id"], "pre_turn")
    assert "ref:" not in summary.model_dump_json()
    if assisted:
        assert summary.continuity_notes[0].evidence[0].quote == clean
    assert "ref:" not in json.dumps(ContextBuilder(store).build(current["id"]).model_context, ensure_ascii=False)
    assert store.read_item(user["id"])["content"] == raw


def test_grounding_accepts_original_and_projected_user_quotes_without_changing_source():
    selected = reference("skill")
    raw = f"使用[$github](ref:{selected.id[:12]})检查。"
    fact = ContextItemFact(item_id="user-1", run_id="run-1", kind="user_message", status="completed",
                           content=raw, input_references=(selected,))
    assert source_text(fact) == raw
    assert model_source_text(fact) == "使用$github检查。"
    for quote in (raw, "使用$github检查。"):
        assert ground_note("decision", (CompactNoteEvidence(item_id=fact.item_id, quote=quote),),
                           {fact.item_id: fact}).kind == "decision"
    with pytest.raises(ValueError, match="summary_quote_invalid"):
        ground_note("decision", (CompactNoteEvidence(item_id=fact.item_id, quote="用户批准提交"),),
                    {fact.item_id: fact})
    for kind in ("assistant_message", "tool_call"):
        assert model_source_text(fact.model_copy(update={"kind": kind})) == raw
