from __future__ import annotations

from collections.abc import Callable

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.domain.personalization import PersonalizationConflict
from eidos_runtime.persistence.personalization import PersonalizationRepository
from eidos_runtime.protocol.personalization import PersonalizationResponse, PersonalizationSaveRequest
from eidos_runtime.sandbox.sensitive import SensitiveContentDenied, SensitiveScanError


class PersonalizationApplication:
    def __init__(self, repository: PersonalizationRepository, scan_text: Callable[[str], str]) -> None:
        self.repository = repository
        self.scan_text = scan_text

    def read(self) -> PersonalizationResponse:
        return PersonalizationResponse.model_validate(self.repository.read().model_dump())

    def save(self, request: PersonalizationSaveRequest) -> PersonalizationResponse:
        try:
            for value in request.preferences.model_dump(mode="json").values():
                if isinstance(value, str) and self.scan_text(value) != value:
                    raise ApplicationError("SENSITIVE_CONTENT_REJECTED", "请移除凭据等敏感内容后再保存。")
        except SensitiveContentDenied as error:
            raise ApplicationError("SENSITIVE_CONTENT_REJECTED", "请移除凭据等敏感内容后再保存。") from error
        except SensitiveScanError as error:
            raise ApplicationError("SENSITIVE_SCAN_FAILED", "内容检查暂不可用，请稍后重试。") from error
        try:
            saved = self.repository.save(request.preferences, request.expected_revision)
        except PersonalizationConflict as error:
            raise ApplicationError("PERSONALIZATION_CONFLICT", str(error)) from error
        return PersonalizationResponse.model_validate(saved.model_dump())
