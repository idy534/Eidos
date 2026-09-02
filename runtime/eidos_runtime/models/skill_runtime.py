from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import Field, StrictBool, StrictStr, field_validator

from eidos_runtime.models import EidosFrozenStrictModel


MAX_RUNTIME_DEPENDENCIES = 32
MAX_RUNTIME_NAME_CHARS = 128
MAX_RUNTIME_IMPORT_NAME_CHARS = MAX_RUNTIME_NAME_CHARS
MAX_RUNTIME_VERSION_CHARS = 128

_PYTHON_PACKAGE_NAME = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
_PYTHON_IMPORT_NAME = r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$"
_NODE_PACKAGE_NAME = (
    r"^(?:[A-Za-z0-9][A-Za-z0-9._~-]*|"
    r"@[A-Za-z0-9][A-Za-z0-9._~-]*/[A-Za-z0-9][A-Za-z0-9._~-]*)$"
)
_EXECUTABLE_NAME = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"


def _validate_version(value: str) -> str:
    if not value.strip() or any(
        ord(character) < 0x20 or ord(character) == 0x7F for character in value
    ):
        raise ValueError("version must be non-blank and free of control characters")
    return value


class _VersionedRequirement(EidosFrozenStrictModel):
    version: StrictStr = Field(
        min_length=1,
        max_length=MAX_RUNTIME_VERSION_CHARS,
    )
    required: StrictBool = True

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        return _validate_version(value)


class PythonPackageRequirement(_VersionedRequirement):
    kind: Literal["python-package"]
    name: StrictStr = Field(
        min_length=1,
        max_length=MAX_RUNTIME_NAME_CHARS,
        pattern=_PYTHON_PACKAGE_NAME,
    )
    import_name: StrictStr = Field(
        min_length=1,
        max_length=MAX_RUNTIME_IMPORT_NAME_CHARS,
        pattern=_PYTHON_IMPORT_NAME,
    )


class NodePackageRequirement(_VersionedRequirement):
    kind: Literal["node-package"]
    name: StrictStr = Field(
        min_length=1,
        max_length=MAX_RUNTIME_NAME_CHARS,
        pattern=_NODE_PACKAGE_NAME,
    )


class ExecutableRequirement(_VersionedRequirement):
    kind: Literal["executable"]
    name: StrictStr = Field(
        min_length=1,
        max_length=MAX_RUNTIME_NAME_CHARS,
        pattern=_EXECUTABLE_NAME,
    )


RuntimeDependency: TypeAlias = Annotated[
    PythonPackageRequirement | NodePackageRequirement | ExecutableRequirement,
    Field(discriminator="kind"),
]


class RuntimeRequirements(EidosFrozenStrictModel):
    schema_version: Literal[1]
    dependencies: tuple[RuntimeDependency, ...] = Field(
        min_length=1,
        max_length=MAX_RUNTIME_DEPENDENCIES,
    )

    @field_validator("dependencies", mode="before")
    @classmethod
    def accept_yaml_sequence(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value

    @field_validator("schema_version", mode="before")
    @classmethod
    def reject_boolean_schema_version(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("schemaVersion must be exactly 1")
        return value


__all__ = [
    "MAX_RUNTIME_DEPENDENCIES",
    "MAX_RUNTIME_IMPORT_NAME_CHARS",
    "MAX_RUNTIME_NAME_CHARS",
    "MAX_RUNTIME_VERSION_CHARS",
    "ExecutableRequirement",
    "NodePackageRequirement",
    "PythonPackageRequirement",
    "RuntimeDependency",
    "RuntimeRequirements",
]
