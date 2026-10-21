from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import Field

from eidos_runtime.models import EidosFrozenStrictModel


class ShellEnvironmentConfigConflict(RuntimeError):
    pass


CORE_ENVIRONMENT_NAMES = frozenset(
    (
        "PATH",
        "SHELL",
        "HOME",
        "TMPDIR",
        "TEMP",
        "TMP",
        "USER",
        "LOGNAME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
    )
)


class ShellEnvironmentPolicy(EidosFrozenStrictModel):
    """Child environment inheritance, independent of filesystem permissions."""

    inherit: Literal["all", "core", "none"] = "all"

    def apply(self, environment: Mapping[str, str]) -> dict[str, str]:
        return {
            key: value
            for key, value in environment.items()
            if not key.upper().startswith("EIDOS_")
            and (
                self.inherit == "all"
                or (self.inherit == "core" and key.upper() in CORE_ENVIRONMENT_NAMES)
            )
        }


class ShellEnvironmentSettings(EidosFrozenStrictModel):
    revision: int = Field(default=0, ge=0, le=9_007_199_254_740_991)
    policy: ShellEnvironmentPolicy = Field(default_factory=ShellEnvironmentPolicy)
