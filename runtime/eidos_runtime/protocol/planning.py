from pydantic import Field, model_validator
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
    goal_mode: bool = False
    goal_revision: int | None = Field(default=None, ge=1, le=9_007_199_254_740_991)
    plan_id: str | None = None
    plan_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def exclusive_modes(self) -> "RunPlanningOptions":
        if self.goal_mode and (self.work_mode != "execute" or self.plan_id is not None or self.plan_revision is not None):
            raise ValueError("goal mode and plan mode are mutually exclusive")
        if self.goal_revision is not None and (self.work_mode != "execute" or self.plan_id is not None or self.plan_revision is not None):
            raise ValueError("goal continuation and plan mode are mutually exclusive")
        return self
