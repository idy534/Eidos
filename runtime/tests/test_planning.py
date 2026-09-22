from __future__ import annotations

import json
from pathlib import Path

import pytest

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.planning import (
    InputAnswer,
    InputOption,
    InputQuestion,
    RequestUserInput,
    UserInputResponse,
    WritePlan,
)
from eidos_runtime.persistence.planning import PlanningRepository
from eidos_runtime.tools.planning import planning_entries


def _store(tmp_path: Path) -> tuple[SessionStore, dict[str, object]]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    return store, store.create_session(str(workspace))


def test_plan_revisions_are_persisted_and_external_file_edits_fail_closed(tmp_path: Path) -> None:
    store, session = _store(tmp_path)
    try:
        run, _ = store.create_run(
            str(session["id"]), "make a plan", work_mode="plan"
        )
        repository = PlanningRepository(store.database)

        first = repository.write(
            str(run["id"]),
            WritePlan(title="First plan", markdown="# First", ready_for_review=True),
        )
        assert store.read_run(str(run["id"]))["workMode"] == "plan"
        assert first.status == "review"
        assert Path(first.path).read_text(encoding="utf-8") == "# First"

        second = repository.write(
            str(run["id"]),
            WritePlan(
                plan_id=first.id,
                expected_revision=first.revision,
                title="First plan",
                markdown="# Second",
                ready_for_review=True,
            ),
        )
        assert second.revision == 2
        assert [row[0] for row in store.connection.execute(
            "SELECT revision FROM plan_revisions WHERE plan_id = ? ORDER BY revision",
            (first.id,),
        )] == [1, 2]

        Path(second.path).write_text("external edit", encoding="utf-8")
        with pytest.raises(ValueError, match="plan_file_has_external_changes"):
            repository.write(
                str(run["id"]),
                WritePlan(
                    plan_id=second.id,
                    expected_revision=second.revision,
                    title="First plan",
                    markdown="# Third",
                ),
            )
        assert repository.read(second.id).revision == 2
    finally:
        store.close()


def test_confirming_a_plan_consumes_one_revision_and_is_idempotently_rejected(tmp_path: Path) -> None:
    store, session = _store(tmp_path)
    try:
        planning_run, _ = store.create_run(
            str(session["id"]), "make a plan", work_mode="plan"
        )
        repository = PlanningRepository(store.database)
        plan = repository.write(
            str(planning_run["id"]),
            WritePlan(title="Ready", markdown="# Ready", ready_for_review=True),
        )
        store.fail_run(str(planning_run["id"]), "plan_ready")

        execution_run, _ = store.create_run(
            str(session["id"]),
            "execute the plan",
            work_mode="execute",
            plan_id=plan.id,
            plan_revision=plan.revision,
        )
        accepted = store.connection.execute(
            "SELECT status, execution_run_id FROM plans WHERE id = ?", (plan.id,)
        ).fetchone()
        assert tuple(accepted) == ("accepted", execution_run["id"])

        store.fail_run(str(execution_run["id"]), "test_done")
        with pytest.raises(ValueError, match="plan_not_awaiting_confirmation"):
            store.create_run(
                str(session["id"]),
                "execute again",
                work_mode="execute",
                plan_id=plan.id,
                plan_revision=plan.revision,
            )
    finally:
        store.close()


def test_user_input_moves_the_run_to_waiting_input_then_requeues_once_answered(tmp_path: Path) -> None:
    store, session = _store(tmp_path)
    try:
        run, _ = store.create_run(
            str(session["id"]), "clarify the plan", work_mode="plan"
        )
        item = store.create_tool_item(
            str(run["id"]), 1, 0, "call-1", "request_user_input",
            json.dumps({"questions": [{
                "id": "scope",
                "question": "Which scope?",
                "type": "single_select",
                "options": [{"id": "app", "label": "App"}, {"id": "runtime", "label": "Runtime"}],
            }]}),
        )
        request = RequestUserInput.model_validate_json(
            str(store.read_item(str(item["id"]))["toolCall"]["argumentsJson"])
        )
        repository = PlanningRepository(store.database)
        saved = repository.ask(str(run["id"]), str(item["id"]), request)
        assert saved.status == "pending"
        assert store.read_run(str(run["id"]))["status"] == "waiting_input"

        response = UserInputResponse(
            status="answered",
            answers=[InputAnswer(question_id="scope", option_ids=["runtime"])],
        )
        answered = repository.answer(saved.id, response)
        assert answered.status == "answered"
        assert store.read_run(str(run["id"]))["status"] == "queued"
        assert repository.answer(saved.id, response) == answered

        with pytest.raises(ValueError, match="user_input_already_resolved"):
            repository.answer(saved.id, UserInputResponse(status="skipped"))
    finally:
        store.close()


def test_planning_input_and_tool_contracts_keep_questions_bounded() -> None:
    question = InputQuestion(
        id="scope",
        question="Which scope?",
        options=[InputOption(id="app", label="App"), InputOption(id="runtime", label="Runtime")],
    )
    assert {entry.spec.name for entry in planning_entries()} == {"request_user_input", "write_plan"}
    with pytest.raises(ValueError):
        RequestUserInput(questions=[question, question, question, question])
