"""A Provider prompt cache reuses only an unchanged request prefix.

Within one Run a later request must extend the earlier one. Runtime-authored
sections that change while the Run works belong after the history.
"""
from __future__ import annotations

from pathlib import Path
import threading

import pytest

from eidos_runtime.context.builder import ContextBuilder
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.memory.contracts import MemoryEditRequest, MemoryPolicyRequest
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.persistence.hooks import HookRepository
from eidos_runtime.runtime.engine import RuntimeEngine

pytestmark = pytest.mark.integration

# Sections the Runtime rewrites between requests. They never precede history.
TAIL_SECTIONS = frozenset({
    "hook-feedback", "unavailable-tools", "runtime-feedback", "runtime-loop-recovery",
    "goal-current", "agent-evidence", "runtime-state", "unknown-outcomes",
})
HISTORY_TYPES = frozenset({"assistant", "tool_call", "tool_result"})


@pytest.fixture
def workspace_store(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("fixture\n", encoding="utf-8")
    (workspace / "NOTES.md").write_text("notes\n", encoding="utf-8")
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.create_session(str(workspace))
    try:
        yield store, session
    finally:
        store.close()


def _save_note(store, session, path: str, content: str) -> None:
    store.database.memory.edit(MemoryEditRequest(
        session_id=session["id"], operation_id="note:" + path, path=path,
        summary="用户背景", kind="user", content=content,
    ))


def _memory_index(context):
    return next((item for item in context if item.get("sectionId") == "memory-evidence"), None)


def _stable_part(context):
    return [item for item in context if item.get("sectionId") not in TAIL_SECTIONS]


def test_later_requests_extend_the_earlier_request(workspace_store, monkeypatch):
    store, session = workspace_store
    _save_note(store, session, "profile.md", "用户使用中文。")
    run, _ = store.create_run(session["id"], "Inspect the project")
    hook_feedback: list[str] = []
    monkeypatch.setattr(HookRepository, "context", lambda _self, _run_id: tuple(hook_feedback))

    class WorkingModel(ScriptedModel):
        def complete(self, context, cancel, on_text_delta, **options):
            # Memory and Hook feedback change while the Run works.
            if self._index == 1:
                _save_note(store, session, "other.md", "另一篇笔记")
            if self._index >= 1:
                hook_feedback.append(f"Lint feedback after step {self._index}")
            return super().complete(context, cancel, on_text_delta, **options)

    model = WorkingModel([
        ModelResponse(tool_calls=(ModelToolCall("read-1", "read_file", {"path": "README.md"}),)),
        ModelResponse(tool_calls=(ModelToolCall("read-2", "read_file", {"path": "NOTES.md"}),)),
        ModelResponse(tool_calls=(ModelToolCall("list-1", "list_files", {"path": "."}),)),
        ModelResponse(text="Done"),
    ])

    RuntimeEngine(store, model, lambda _message: None).run(run["id"], threading.Event())

    assert store.read_run(run["id"])["status"] == "succeeded"
    assert len(model.contexts) == 4
    assert _memory_index(model.contexts[0]) is not None
    assert any(item.get("sectionId") == "hook-feedback" for item in model.contexts[-1])
    for earlier, later in zip(model.contexts, model.contexts[1:]):
        stable = _stable_part(earlier)
        assert _stable_part(later)[:len(stable)] == stable
    for context in model.contexts:
        history = [index for index, item in enumerate(context) if item.get("type") in HISTORY_TYPES]
        changing = [index for index, item in enumerate(context) if item.get("sectionId") in TAIL_SECTIONS]
        if history and changing:
            assert min(changing) > max(history)


def test_memory_index_is_fixed_for_one_run_and_follows_revocation(workspace_store):
    store, session = workspace_store
    _save_note(store, session, "profile.md", "用户使用中文。")
    run, _ = store.create_run(session["id"], "Inspect the project")
    builder = ContextBuilder(store)

    first = _memory_index(builder.build(run["id"]).model_context)
    _save_note(store, session, "other.md", "另一篇笔记")
    same_run = _memory_index(builder.build(run["id"]).model_context)
    next_run = _memory_index(ContextBuilder(store).build(run["id"]).model_context)

    assert first is not None and "profile.md" in first["content"]
    assert same_run == first
    assert next_run is not None and "other.md" in next_run["content"]

    for scope in ("current", "global"):
        store.database.memory.policy(
            MemoryPolicyRequest(session_id=session["id"], scope=scope, mode="off")
        )
    # Switching reading off is a privacy change: it applies to the same Run.
    assert _memory_index(builder.build(run["id"]).model_context) is None


def test_memory_index_budget_does_not_follow_the_remaining_space(workspace_store, monkeypatch):
    store, session = workspace_store
    _save_note(store, session, "profile.md", "用户使用中文。")
    run, _ = store.create_run(session["id"], "Inspect the project")
    old = store.create_tool_item(run["id"], 1, 0, "old-read", "read_file", "{}")
    store.complete_tool_item(old["id"], '{"outcome":"success","data":{"content":"' + "x" * 40_000 + '"}}')
    service = store.database.memory
    project = service.project
    budgets: list[int] = []

    def recording(session_id, available_tokens, *, run_id):
        budgets.append(available_tokens)
        return project(session_id, available_tokens, run_id=run_id)

    monkeypatch.setattr(service, "project", recording)

    built = ContextBuilder(store).build(run["id"])

    assert budgets == [built.budget.usable_input_budget]
    assert built.budget.projected_input_tokens > 4_000
