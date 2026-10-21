from __future__ import annotations

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.domain.shell_environment import ShellEnvironmentConfigConflict
from eidos_runtime.persistence.shell_environment import ShellEnvironmentRepository
from eidos_runtime.protocol.shell_environment import (
    ShellEnvironmentResponse,
    ShellEnvironmentSaveRequest,
)
from eidos_runtime.sandbox.host_shell import SHELL_ENVIRONMENT_PROVIDER


class ShellEnvironmentApplication:
    def __init__(self, repository: ShellEnvironmentRepository) -> None:
        self.repository = repository

    def read(self) -> ShellEnvironmentResponse:
        return ShellEnvironmentResponse.model_validate(
            self.repository.read().model_dump()
        )

    def save(self, request: ShellEnvironmentSaveRequest) -> ShellEnvironmentResponse:
        try:
            saved = self.repository.save(request.policy, request.expected_revision)
        except ShellEnvironmentConfigConflict as error:
            raise ApplicationError(
                "SHELL_ENVIRONMENT_CONFIG_CONFLICT", str(error)
            ) from error
        SHELL_ENVIRONMENT_PROVIDER.invalidate()
        return ShellEnvironmentResponse.model_validate(saved.model_dump())

    def refresh(self) -> ShellEnvironmentResponse:
        SHELL_ENVIRONMENT_PROVIDER.invalidate()
        return self.read()
