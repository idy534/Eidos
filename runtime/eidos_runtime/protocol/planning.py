from pydantic import Field
from eidos_runtime.models import EidosFrozenStrictModel
from eidos_runtime.domain.planning import PlanDocument, UserInputRequest, UserInputResponse, WorkMode


class PlanningReadRequest(EidosFrozenStrictModel):
    session_id: str


class PlanningReadResponse(EidosFrozenStrictModel):
    plans: list[PlanDocument]
    questions: list[UserInputRequest]


class AnswerInputRequest(EidosFrozenStrictModel):
    request_id: str
    response: UserInputResponse


class AnswerInputResponse(EidosFrozenStrictModel):
    request: UserInputRequest


class PlanEditRequest(EidosFrozenStrictModel):
    plan_id: str
    expected_revision: int = Field(ge=1)
    markdown: str = Field(min_length=1, max_length=65536)


class PlanReadRequest(EidosFrozenStrictModel):
    plan_id: str
    reload_file: bool = False


class PlanResponse(EidosFrozenStrictModel):
    plan: PlanDocument


class RunPlanningOptions(EidosFrozenStrictModel):
    work_mode: WorkMode = 'execute'
    plan_id: str | None = None
    plan_revision: int | None = Field(default=None, ge=1)
