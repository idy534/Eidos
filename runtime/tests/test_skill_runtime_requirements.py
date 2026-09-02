from __future__ import annotations

import pytest
from pydantic import ValidationError

from eidos_runtime.models.skill_runtime import (
    MAX_RUNTIME_DEPENDENCIES,
    MAX_RUNTIME_NAME_CHARS,
    MAX_RUNTIME_VERSION_CHARS,
    ExecutableRequirement,
    NodePackageRequirement,
    PythonPackageRequirement,
    RuntimeRequirements,
)


def _python_requirement(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "kind": "python-package",
        "name": "python-docx",
        "importName": "docx",
        "version": ">=1.2,<2",
        "required": True,
    }
    value.update(overrides)
    return value


def _node_requirement(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "kind": "node-package",
        "name": "docx",
        "version": "9.6.1",
        "required": True,
    }
    value.update(overrides)
    return value


def _executable_requirement(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "kind": "executable",
        "name": "node",
        "version": ">=22",
        "required": True,
    }
    value.update(overrides)
    return value


def _requirements_payload() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "dependencies": [
            _python_requirement(),
            _node_requirement(),
            _executable_requirement(),
        ],
    }


def test_runtime_requirements_preserve_typed_discriminated_dependencies() -> None:
    requirements = RuntimeRequirements.model_validate(_requirements_payload())

    assert requirements.schema_version == 1
    assert isinstance(requirements.dependencies[0], PythonPackageRequirement)
    assert requirements.dependencies[0].import_name == "docx"
    assert isinstance(requirements.dependencies[1], NodePackageRequirement)
    assert isinstance(requirements.dependencies[2], ExecutableRequirement)
    assert requirements.to_wire_dict() == _requirements_payload()


def test_runtime_dependency_required_defaults_to_true() -> None:
    dependency = _node_requirement()
    dependency.pop("required")

    requirements = RuntimeRequirements.model_validate(
        {"schemaVersion": 1, "dependencies": [dependency]}
    )

    assert requirements.dependencies[0].required is True


@pytest.mark.parametrize(
    "payload",
    [
        {**_requirements_payload(), "schemaVersion": 2},
        {**_requirements_payload(), "schemaVersion": True},
        {**_requirements_payload(), "schemaVersion": "1"},
        {**_requirements_payload(), "unknown": True},
        {
            **_requirements_payload(),
            "dependencies": [_python_requirement(unknown=True)],
        },
        {
            **_requirements_payload(),
            "dependencies": [_python_requirement(required="true")],
        },
    ],
)
def test_runtime_requirements_reject_invalid_version_types_and_unknown_fields(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        RuntimeRequirements.model_validate(payload)


@pytest.mark.parametrize(
    ("kind", "field", "value"),
    [
        ("python-package", "name", "../escape"),
        ("python-package", "name", "pkg/name"),
        ("python-package", "importName", "pkg/loader"),
        ("python-package", "importName", "pkg..loader"),
        ("node-package", "name", "../escape"),
        ("node-package", "name", "scope/name"),
        ("node-package", "name", "@scope/../escape"),
        ("executable", "name", "/usr/bin/node"),
        ("executable", "name", "../node"),
    ],
)
def test_runtime_requirements_reject_path_unsafe_names(
    kind: str,
    field: str,
    value: str,
) -> None:
    requirement = {
        "python-package": _python_requirement,
        "node-package": _node_requirement,
        "executable": _executable_requirement,
    }[kind](**{field: value})
    payload = {"schemaVersion": 1, "dependencies": [requirement]}

    with pytest.raises(ValidationError):
        RuntimeRequirements.model_validate(payload)


@pytest.mark.parametrize(
    ("kind", "field", "limit"),
    [
        ("python-package", "name", MAX_RUNTIME_NAME_CHARS),
        ("python-package", "importName", MAX_RUNTIME_NAME_CHARS),
        ("python-package", "version", MAX_RUNTIME_VERSION_CHARS),
        ("node-package", "name", MAX_RUNTIME_NAME_CHARS),
        ("node-package", "version", MAX_RUNTIME_VERSION_CHARS),
        ("executable", "name", MAX_RUNTIME_NAME_CHARS),
        ("executable", "version", MAX_RUNTIME_VERSION_CHARS),
    ],
)
def test_runtime_requirements_reject_oversized_fields(
    kind: str,
    field: str,
    limit: int,
) -> None:
    constructors = {
        "python-package": _python_requirement,
        "node-package": _node_requirement,
        "executable": _executable_requirement,
    }
    requirement = constructors[kind](**{field: "a" * (limit + 1)})

    with pytest.raises(ValidationError):
        RuntimeRequirements.model_validate(
            {"schemaVersion": 1, "dependencies": [requirement]}
        )


def test_runtime_requirements_reject_too_many_dependencies() -> None:
    dependencies = [_executable_requirement() for _ in range(MAX_RUNTIME_DEPENDENCIES + 1)]

    with pytest.raises(ValidationError):
        RuntimeRequirements.model_validate(
            {"schemaVersion": 1, "dependencies": dependencies}
        )


@pytest.mark.parametrize(
    "dependency",
    [
        _python_requirement(version=1),
        _node_requirement(version=True),
        _executable_requirement(required=1),
        {"kind": "unknown", "name": "node", "version": "1", "required": True},
    ],
)
def test_runtime_requirements_reject_invalid_requirement_shapes(
    dependency: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        RuntimeRequirements.model_validate(
            {"schemaVersion": 1, "dependencies": [dependency]}
        )


@pytest.mark.parametrize(
    "version",
    ["", "   ", "1\n2", "1\r2", "1\t2", "1\x002"],
)
def test_runtime_requirements_reject_blank_and_control_character_versions(
    version: str,
) -> None:
    with pytest.raises(ValidationError):
        RuntimeRequirements.model_validate(
            {
                "schemaVersion": 1,
                "dependencies": [_python_requirement(version=version)],
            }
        )


def test_runtime_requirements_keep_version_range_syntax_opaque() -> None:
    requirements = RuntimeRequirements.model_validate(
        {
            "schemaVersion": 1,
            "dependencies": [_python_requirement(version=">=1.2, <2")],
        }
    )

    assert requirements.dependencies[0].version == ">=1.2, <2"
