from __future__ import annotations

from typing import Literal
from pydantic import Field, model_validator
from eidos_runtime.models import EidosFrozenStrictModel, JsonSafeInt

WorkMode = Literal['execute', 'plan']


class InputOption(EidosFrozenStrictModel):
    id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=200)
    description: str = Field(default='', max_length=500)


class InputQuestion(EidosFrozenStrictModel):
    id: str = Field(min_length=1, max_length=80)
    question: str = Field(min_length=1, max_length=1000)
    type: Literal['single_select', 'multi_select', 'text'] = Field(default='single_select', description='Use single_select or multi_select for choices with 2–6 options. Use text for a free-text question, with no options or recommendedOptionId.')
    options: list[InputOption] = Field(default_factory=list, max_length=6, description='Each choice has a unique id and a label. Required with 2–6 entries for choice questions; omit for text. The UI provides custom input; do not add an Other/custom option.')
    recommended_option_id: str | None = Field(default=None, description='Optional existing option id, not its label. Omit for text questions.')

    @model_validator(mode='after')
    def validate_options(self):
        ids = [option.id for option in self.options]
        if len(ids) != len(set(ids)):
            raise ValueError('duplicate_option_id')
        if self.type == 'text' and self.options:
            raise ValueError('text_question_has_options')
        if self.type != 'text' and len(ids) < 2:
            raise ValueError('choice_question_requires_options')
        if self.recommended_option_id is not None and self.recommended_option_id not in ids:
            raise ValueError('unknown_recommended_option')
        return self


class RequestUserInput(EidosFrozenStrictModel):
    questions: list[InputQuestion] = Field(min_length=1, max_length=3, description=(
        'Put every question inside this array; the only top-level field is questions. '
        'Every question needs a unique id and question text. Example: '
        '{"questions":[{"id":"tone","question":"Which tone?","type":"single_select",'
        '"options":[{"id":"formal","label":"Formal"},{"id":"casual","label":"Casual"}],'
        '"recommendedOptionId":"formal"},{"id":"constraints","question":"Any constraints?","type":"text"}]}'
    ))

    @model_validator(mode='after')
    def unique_questions(self):
        if len({q.id for q in self.questions}) != len(self.questions):
            raise ValueError('duplicate_question_id')
        return self


class InputAnswer(EidosFrozenStrictModel):
    question_id: str = Field(min_length=1, max_length=80)
    option_ids: list[str] = Field(default_factory=list, max_length=6)
    text: str = Field(default='', max_length=8000)


class UserInputResponse(EidosFrozenStrictModel):
    status: Literal['answered', 'skipped']
    answers: list[InputAnswer] = Field(default_factory=list, max_length=3)


class UserInputRequest(EidosFrozenStrictModel):
    id: str
    session_id: str
    run_id: str
    item_id: str
    questions: list[InputQuestion]
    status: Literal['pending', 'answered', 'skipped', 'canceled']
    response: UserInputResponse | None = None
    created_at: JsonSafeInt


class PlanDocument(EidosFrozenStrictModel):
    id: str
    session_id: str
    run_id: str
    revision: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    markdown: str = Field(min_length=1, max_length=65536)
    sha256: str
    status: Literal['draft', 'review', 'accepted']
    path: str
    updated_at: JsonSafeInt
    execution_run_id: str | None = None


class WritePlan(EidosFrozenStrictModel):
    plan_id: str | None = Field(default=None, description="Omit for a new plan. For revisions, use the exact ID returned by write_plan; never invent an ID.")
    expected_revision: int | None = Field(default=None, ge=1, description="Omit for a new plan. For revisions, use the current revision returned by write_plan.")
    title: str = Field(min_length=1, max_length=200)
    markdown: str = Field(min_length=1, max_length=65536)
    ready_for_review: bool = False


class PlanningSuspended(Exception):
    """A durable user-input request owns the continuation; no worker waits."""
