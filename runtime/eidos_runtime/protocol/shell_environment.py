from __future__ import annotations

from pydantic import ConfigDict, Field

from eidos_runtime.domain.shell_environment import ShellEnvironmentPolicy
from eidos_runtime.models import EidosFrozenStrictModel


class ShellEnvironmentReadRequest(EidosFrozenStrictModel):
    pass


class ShellEnvironmentRefreshRequest(EidosFrozenStrictModel):
    pass


class ShellEnvironmentSaveRequest(EidosFrozenStrictModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    expected_revision: int = Field(ge=0, le=9_007_199_254_740_991)
    policy: ShellEnvironmentPolicy


class ShellEnvironmentResponse(EidosFrozenStrictModel):
    revision: int = Field(ge=0, le=9_007_199_254_740_991)
    policy: ShellEnvironmentPolicy
