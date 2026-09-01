from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path

from pydantic import Field, ValidationInfo, field_validator

from eidos_runtime.models import EidosFrozenStrictModel


_PYTHON_CONTROL_ENV_NAMES = frozenset(
    {
        "PYTHONASYNCIODEBUG",
        "PYTHONBREAKPOINT",
        "PYTHONCOERCECLOCALE",
        "PYTHONDEBUG",
        "PYTHONDEVMODE",
        "PYTHONEXECUTABLE",
        "PYTHONFAULTHANDLER",
        "PYTHONHASHSEED",
        "PYTHONHOME",
        "PYTHONINSPECT",
        "PYTHONINTMAXSTRDIGITS",
        "PYTHONIOENCODING",
        "PYTHONLEGACYWINDOWSFSENCODING",
        "PYTHONLEGACYWINDOWSSTDIO",
        "PYTHONMALLOC",
        "PYTHONNODEBUGRANGES",
        "PYTHONNOUSERSITE",
        "PYTHONOPTIMIZE",
        "PYTHONPATH",
        "PYTHONPROFILEIMPORTTIME",
        "PYTHONPYCACHEPREFIX",
        "PYTHONSAFEPATH",
        "PYTHONSTARTUP",
        "PYTHONTRACEMALLOC",
        "PYTHONUNBUFFERED",
        "PYTHONVERBOSE",
        "PYTHONUSERBASE",
        "PYTHONUTF8",
        "PYTHONWARNDEFAULTENCODING",
        "PYTHONWARNINGS",
        "PYTHONDONTWRITEBYTECODE",
    }
)
_NODE_CONTROL_ENV_NAMES = frozenset(
    {
        "NODE_CHANNEL_FD",
        "NODE_COMPILE_CACHE",
        "NODE_DEBUG",
        "NODE_DEBUG_NATIVE",
        "NODE_DISABLE_COLORS",
        "NODE_DISABLE_COMPILE_CACHE",
        "NODE_EXTRA_CA_CERTS",
        "NODE_ICU_DATA",
        "NODE_NO_WARNINGS",
        "NODE_OPTIONS",
        "NODE_PATH",
        "NODE_PENDING_DEPRECATION",
        "NODE_PRESERVE_SYMLINKS",
        "NODE_PRESERVE_SYMLINKS_MAIN",
        "NODE_REPL_EXTERNAL_MODULE",
        "NODE_REPL_HISTORY",
        "NODE_REDIRECT_WARNINGS",
        "NODE_SKIP_PLATFORM_CHECK",
        "NODE_TEST_CONTEXT",
        "NODE_TLS_REJECT_UNAUTHORIZED",
        "NODE_USE_ENV_PROXY",
        "NODE_USE_SYSTEM_CA",
        "NODE_V8_COVERAGE",
    }
)
_DEPENDENCY_CONTROL_ENV_NAMES = frozenset(
    {
        "RUNTIME_PYTHON",
        "RUNTIME_NODE",
        "RUNTIME_NODE_MODULES",
        "RUNTIME_BIN_DIR",
    }
    | _PYTHON_CONTROL_ENV_NAMES
    | _NODE_CONTROL_ENV_NAMES
)


class DependencyShellEnvironment(EidosFrozenStrictModel):
    """A validated, immutable environment binding for one Shell launch.

    The binding contains trusted path values selected by the dependency
    catalog.  It deliberately does not inspect the filesystem.  The handler
    that admits a binding owns that verification and protection boundary.
    """

    binding_id: str = Field(min_length=1, max_length=512)
    python_executable: str | None = None
    python_path: tuple[str, ...] = ()
    node_executable: str | None = None
    node_modules: str | None = None
    node_loader: str | None = None
    bin_paths: tuple[str, ...] = ()

    @field_validator("binding_id")
    @classmethod
    def validate_binding_id(cls, value: str) -> str:
        return _validate_text(value, "binding_id")

    @field_validator(
        "python_executable",
        "node_executable",
        "node_modules",
        "node_loader",
    )
    @classmethod
    def validate_optional_absolute_path(
        cls,
        value: str | None,
        info: ValidationInfo,
    ) -> str | None:
        if value is None:
            return None
        return _validate_absolute_path(value, info.field_name)

    @field_validator("python_path", "bin_paths")
    @classmethod
    def validate_absolute_paths(
        cls,
        values: tuple[str, ...],
        info: ValidationInfo,
    ) -> tuple[str, ...]:
        return tuple(
            _validate_absolute_path(value, f"{info.field_name}[{index}]")
            for index, value in enumerate(values)
        )


def apply_dependency_environment(
    environment: Mapping[str, str],
    dependency_environment: DependencyShellEnvironment | None,
) -> dict[str, str]:
    """Return a child-only environment with an optional trusted binding.

    ``None`` preserves the existing environment values exactly.  A binding
    clears inherited runtime-control variables before applying only the
    selected values.  The caller's mapping and the process-global environment
    are never modified.
    """

    effective = dict(environment)
    if dependency_environment is None:
        return effective

    for name in _DEPENDENCY_CONTROL_ENV_NAMES:
        effective.pop(name, None)

    runtime_bin_paths = _runtime_bin_paths(dependency_environment)
    if runtime_bin_paths:
        host_path = effective.get("PATH", "")
        effective["PATH"] = os.pathsep.join(
            (*runtime_bin_paths, host_path)
            if host_path
            else runtime_bin_paths
        )
        # This variable has a singular name by contract.  It identifies the
        # first runtime PATH entry; PATH carries the complete directory list.
        effective["RUNTIME_BIN_DIR"] = runtime_bin_paths[0]

    if dependency_environment.python_executable is not None:
        effective["RUNTIME_PYTHON"] = dependency_environment.python_executable
    if dependency_environment.python_path:
        effective["PYTHONPATH"] = os.pathsep.join(
            dependency_environment.python_path
        )
    effective["PYTHONNOUSERSITE"] = "1"
    effective["PYTHONDONTWRITEBYTECODE"] = "1"
    effective["PYTHONSAFEPATH"] = "1"

    if dependency_environment.node_executable is not None:
        effective["RUNTIME_NODE"] = dependency_environment.node_executable
    if dependency_environment.node_modules is not None:
        effective["RUNTIME_NODE_MODULES"] = dependency_environment.node_modules
    if dependency_environment.node_loader is not None:
        effective["NODE_OPTIONS"] = (
            "--import="
            f"{Path(dependency_environment.node_loader).as_uri()}"
        )

    return effective


def _runtime_bin_paths(
    dependency_environment: DependencyShellEnvironment,
) -> tuple[str, ...]:
    candidates = (
        *dependency_environment.bin_paths,
        *tuple(
            str(Path(executable).parent)
            for executable in (
                dependency_environment.python_executable,
                dependency_environment.node_executable,
            )
            if executable is not None
        ),
    )
    return tuple(dict.fromkeys(candidates))


def _validate_absolute_path(value: str, field_name: str) -> str:
    if not value or "\x00" in value or not os.path.isabs(value):
        raise ValueError(f"{field_name} must be an absolute path")
    return value


def _validate_text(value: str, field_name: str) -> str:
    if "\x00" in value:
        raise ValueError(f"{field_name} is invalid")
    return value


__all__ = [
    "DependencyShellEnvironment",
    "apply_dependency_environment",
]
