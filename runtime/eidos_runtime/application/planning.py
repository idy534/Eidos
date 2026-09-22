from collections.abc import Callable

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.persistence.planning import PlanningRepository
from eidos_runtime.protocol.planning import (
    AnswerInputRequest, AnswerInputResponse, PlanEditRequest, PlanReadRequest,
    PlanResponse, PlanningReadRequest, PlanningReadResponse,
)


class PlanningApplication:
    def __init__(self, store: SessionStore, schedule: Callable[[], None], publish: Callable[[], object], scan: Callable[[str], str]):
        self.repository = PlanningRepository(store.database)
        self.schedule = schedule
        self.publish = publish
        self.scan = scan

    def read(self, request: PlanningReadRequest) -> PlanningReadResponse:
        return PlanningReadResponse(plans=self.repository.list_plans(request.session_id),
                                    questions=self.repository.requests(request.session_id))

    def answer(self, request: AnswerInputRequest) -> AnswerInputResponse:
        response = request.response.model_copy(update={'answers': [answer.model_copy(update={'text': self.scan(answer.text)}) for answer in request.response.answers]})
        try:
            result = self.repository.answer(request.request_id, response)
        except ValueError as error:
            raise ApplicationError('INVALID_STATE', str(error)) from error
        self.publish()
        self.schedule()
        return AnswerInputResponse(request=result)

    def edit(self, request: PlanEditRequest) -> PlanResponse:
        try:
            plan = self.repository.edit(request.plan_id, request.expected_revision, self.scan(request.markdown))
        except (ValueError, OSError, UnicodeError) as error:
            raise ApplicationError('INVALID_STATE', str(error)) from error
        self.publish()
        return PlanResponse(plan=plan)

    def read_plan(self, request: PlanReadRequest) -> PlanResponse:
        try:
            plan = self.repository.read(request.plan_id)
            text = self.repository.file_text(plan)
            if text is None:
                self.repository.materialize(plan)
            elif text != plan.markdown and request.reload_file:
                plan = self.repository.edit(plan.id, plan.revision, self.scan(text))
            self.publish()
            return PlanResponse(plan=plan)
        except (ValueError, OSError, UnicodeError) as error:
            raise ApplicationError('INVALID_STATE', str(error)) from error
