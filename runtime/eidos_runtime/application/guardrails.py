from __future__ import annotations

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.domain.guardrails import GuardrailConfigConflict
from eidos_runtime.persistence.guardrails import GuardrailSettingsRepository
from eidos_runtime.protocol.guardrails import GuardrailsResponse, GuardrailsSaveRequest


class GuardrailsApplication:
    def __init__(self, repository: GuardrailSettingsRepository) -> None:
        self.repository = repository

    def read(self) -> GuardrailsResponse:
        return GuardrailsResponse.model_validate(self.repository.read().model_dump())

    def save(self, request: GuardrailsSaveRequest) -> GuardrailsResponse:
        try:
            saved = self.repository.save(request.policy, request.expected_revision)
        except GuardrailConfigConflict as error:
            raise ApplicationError("GUARDRAIL_CONFIG_CONFLICT", str(error)) from error
        return GuardrailsResponse.model_validate(saved.model_dump())
