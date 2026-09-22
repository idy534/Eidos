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


@pytest.fixture
def planning_runtime(tmp_path):
    from eidos_runtime.runtime.events import RuntimeEvents
    from eidos_runtime.runtime.state_machine import RuntimePhaseTracker
    from eidos_runtime.runtime.tool_dispatcher import ToolDispatcher
    from eidos_runtime.runtime.tool_runtime import ToolCallRuntime
    from eidos_runtime.sandbox.permissions import BasePermissionProfile
    from eidos_runtime.sandbox.sensitive import default_scanner
    from eidos_runtime.tools.registry import ToolRegistry

    store, session = _store(tmp_path)
    run, _ = store.create_run(str(session['id']), 'plan', work_mode='plan')
    store.increment_model_step(str(run['id']))
    dispatcher = ToolDispatcher(ToolRegistry(planning_entries()))
    runtime = ToolCallRuntime(store, dispatcher, None, RuntimeEvents(lambda _: None),
        default_scanner(), RuntimePhaseTracker(), shell_available=False,
        base_permissions=BasePermissionProfile.for_workspace(workspace_root=tmp_path / 'workspace'))
    yield store, str(run['id']), runtime
    store.close()


def _execute_planning(fixture, name, arguments, item=None):
    import threading
    import uuid
    from eidos_runtime.model.client import ModelToolCall, ModelResponse

    store, run_id, runtime = fixture
    call = ModelToolCall(item['toolCall']['providerCallId'] if item else str(uuid.uuid4()), name, arguments)
    validation = runtime.dispatcher.validate(ModelResponse(tool_calls=(call,)))
    assert validation.error_code is None
    call = validation.tool_calls[0]
    if item is None:
        item = store.create_tool_item(run_id, 1, 0, call.provider_call_id, name, json.dumps(call.arguments))
    outcome = runtime.controller.execute(run_id=run_id, item=item, call=call,
        plan=runtime.dispatcher.plan(call), cancel=threading.Event(), deadline=None)
    return outcome


def test_answer_resumes_original_tool_and_persists_model_result(planning_runtime):
    from eidos_runtime.domain.planning import PlanningSuspended

    store, run_id, _ = planning_runtime
    arguments = {'questions': [{'id': 'genre', 'question': 'What style?', 'type': 'text'}]}
    with pytest.raises(PlanningSuspended):
        _execute_planning(planning_runtime, 'request_user_input', arguments)
    repository = PlanningRepository(store.database)
    request = repository.unfinished(run_id)
    assert request is not None
    repository.answer(request.id, UserInputResponse(status='answered', answers=[
        InputAnswer(question_id='genre', text='散文，约800字'),
    ]))
    assert store.claim_next_run()['id'] == run_id
    outcome = _execute_planning(planning_runtime, 'request_user_input', arguments, store.read_item(request.item_id))
    assert outcome.result['outcome'] == 'success'
    persisted = store.connection.execute('SELECT result_json, model_result_json FROM tool_calls WHERE item_id=?', (request.item_id,)).fetchone()
    for value in persisted:
        assert '散文' in value or '散文' in json.dumps(json.loads(value), ensure_ascii=False)
    assert not store.read_run(run_id).get('reconciliationRequired', False)


def test_write_plan_success_crosses_controller_and_result_projection(planning_runtime):
    store, run_id, _ = planning_runtime
    outcome = _execute_planning(planning_runtime, 'write_plan', {'title': 'Sea', 'markdown': '# 海', 'readyForReview': True})
    assert outcome.result['outcome'] == 'success'
    document = PlanningRepository(store.database).read(outcome.result['data']['planId'])
    assert Path(document.path).read_text() == '# 海'
    assert not store.read_run(run_id).get('reconciliationRequired', False)
    assert store.connection.execute('SELECT status FROM durable_intents WHERE run_id=?', (run_id,)).fetchone()[0] == 'completed'


def test_unknown_plan_id_does_not_create_intent_or_block_retry(planning_runtime):
    store, run_id, _ = planning_runtime
    outcome = _execute_planning(planning_runtime, 'write_plan', {'planId': 'plan-sea-doc', 'title': 'Sea', 'markdown': '# 海'})
    assert outcome.result['code'] == 'plan_not_found'
    assert not outcome.result['reconciliationRequired']
    assert store.connection.execute('SELECT COUNT(*) FROM durable_intents WHERE run_id=?', (run_id,)).fetchone()[0] == 0
    assert not store.read_run(run_id).get('reconciliationRequired', False)
    assert _execute_planning(planning_runtime, 'write_plan', {'title': 'Sea', 'markdown': '# 海'}).result['outcome'] == 'success'


def test_plan_projection_failure_preserves_reconciliation(planning_runtime, monkeypatch):
    def fail_projection(*_args):
        raise OSError('disk unavailable')

    monkeypatch.setattr(PlanningRepository, 'materialize', fail_projection)
    store, run_id, _ = planning_runtime
    outcome = _execute_planning(planning_runtime, 'write_plan', {'title': 'Sea', 'markdown': '# 海'})
    assert outcome.result['reconciliationRequired'] is True
    assert store.read_run(run_id)['reconciliationRequired'] is True
    assert store.connection.execute('SELECT COUNT(*) FROM plans WHERE run_id=?', (run_id,)).fetchone()[0] == 1
    assert store.connection.execute('SELECT status FROM durable_intents WHERE run_id=?', (run_id,)).fetchone()[0] == 'uncertain'


def test_plan_precondition_rechecked_after_intent_is_known_failure(planning_runtime, monkeypatch):
    from eidos_runtime.persistence.planning import PlanWriteRejected

    original = PlanningRepository._prepare_write
    checks = 0

    def reject_second(self, *args):
        nonlocal checks
        checks += 1
        if checks == 2:
            raise PlanWriteRejected('plan_revision_conflict')
        return original(self, *args)

    monkeypatch.setattr(PlanningRepository, '_prepare_write', reject_second)
    store, run_id, _ = planning_runtime
    outcome = _execute_planning(planning_runtime, 'write_plan', {'title': 'Sea', 'markdown': '# 海'})
    assert outcome.result['code'] == 'plan_revision_conflict'
    assert not outcome.result['sideEffectsMayExist']
    assert not store.read_run(run_id).get('reconciliationRequired', False)
    assert store.connection.execute('SELECT COUNT(*) FROM plans WHERE run_id=?', (run_id,)).fetchone()[0] == 0
    assert store.connection.execute('SELECT status FROM durable_intents WHERE run_id=?', (run_id,)).fetchone()[0] == 'completed'


def test_clarification_schema_example_is_valid_and_explains_question_types():
    entry = next(entry for entry in planning_entries() if entry.spec.name == 'request_user_input')
    schema = entry.spec.input_schema
    example = schema['properties']['questions']['description'].split('Example: ', 1)[1]
    assert entry.validate_arguments(json.loads(example)).valid
    question = schema['$defs']['InputQuestion']['properties']
    assert '2–6' in question['type']['description']
    assert 'omit for text' in question['options']['description']
    assert entry.spec.description == 'Request user input for one to three short questions and wait for the response. This tool is only available in Plan mode.'


def test_invalid_clarifications_have_actionable_feedback_and_distinct_error_identity(planning_runtime):
    from eidos_runtime.runtime.tool_runtime import _result_fingerprint

    store, run_id, _ = planning_runtime
    malformed = {'item': {}, 'questions': [{'id': 'tone', 'options': [{'id': 'a'}]}]}
    wrong_type = {'questions': [{'id': 'constraints', 'question': 'Constraints?', 'type': 'text',
                                'options': [{'id': 'a', 'label': 'A'}, {'id': 'b', 'label': 'B'}]}]}
    first = _execute_planning(planning_runtime, 'request_user_input', malformed)
    second = _execute_planning(planning_runtime, 'request_user_input', wrong_type)
    assert first.result['code'] == second.result['code'] == 'invalid_arguments'
    assert 'questions[0].question' in first.result['summary']
    assert 'questions[0].options[0].label' in first.result['summary']
    assert 'For text, omit options and recommendedOptionId' in second.result['summary']
    assert second.argument_validation is not None
    first_hash = _result_fingerprint('request_user_input', first.result, first.argument_validation)
    second_hash = _result_fingerprint('request_user_input', second.result, second.argument_validation)
    assert first_hash != second_hash
    from eidos_runtime.runtime.loop_guard import LoopGuard
    guard = LoopGuard()
    decisions = [guard.observe_progress(guard.make_signature(
        workspace_version=0, diff_hash=None, successful_tool_result_hashes=(),
        context_fact_ids=(), error_fingerprints=(fingerprint,), reconciliation_epoch=0,
    )) for fingerprint in (first_hash, second_hash, second_hash)]
    assert decisions == [None, None, 'recover_no_progress']
    wrong_type['questions'][0]['question'] = 'Different wording?'
    repeated = _execute_planning(planning_runtime, 'request_user_input', wrong_type)
    assert _result_fingerprint('request_user_input', repeated.result, repeated.argument_validation) == second_hash
    assert store.connection.execute('SELECT COUNT(*) FROM user_input_requests WHERE run_id=?', (run_id,)).fetchone()[0] == 0
    assert store.connection.execute('SELECT COUNT(*) FROM durable_intents WHERE run_id=?', (run_id,)).fetchone()[0] == 0
    persisted = store.connection.execute('SELECT model_result_json FROM tool_calls ORDER BY creation_seq DESC LIMIT 1').fetchone()[0]
    assert 'No question was submitted' in json.loads(persisted)['summary']


def test_argument_diagnostics_are_bounded_and_do_not_echo_values():
    entry = next(entry for entry in planning_entries() if entry.spec.name == 'request_user_input')
    validation = entry.validate_arguments({**{f'extra{i}': 'private value' for i in range(20)}, 'questions': []})
    assert not validation.valid
    assert len(validation.issues) == 8
    assert 'private value' not in validation.model_dump_json()
