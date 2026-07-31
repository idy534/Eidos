from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from eidos_runtime.contracts.export_model_profile import export_schema
from eidos_runtime.model_gateway.models import ModelProfile


FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "contracts"
    / "fixtures"
    / "model-profile.json"
)


def test_model_profile_contract_schema_uses_wire_aliases_and_closed_models(
    tmp_path: Path,
) -> None:
    output = tmp_path / "model-profile.schema.json"

    export_schema(output)

    schema = json.loads(output.read_text(encoding="utf-8"))
    definitions = schema["definitions"]
    profile = definitions["ModelProfile"]

    assert schema["$comment"] == (
        "Generated from Eidos Runtime Pydantic models. Do not edit manually."
    )
    assert {"ModelProfile", "RetryPolicy"} <= set(definitions)
    assert "CapabilitySnapshot" not in definitions
    assert "supportsToolCall" not in profile["properties"]
    assert "supportsTools" in profile["properties"]
    assert "authReference" in profile["properties"]
    assert "auth_reference" not in profile["properties"]
    assert profile["additionalProperties"] is False
    assert "id" in profile["required"]
    assert "baseUrl" not in profile["required"]


def test_model_profile_contract_schema_export_is_byte_deterministic(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    export_schema(first)
    export_schema(second)

    assert first.read_bytes() == second.read_bytes()


def test_model_profile_contract_export_module_writes_the_requested_path(
    tmp_path: Path,
) -> None:
    output = tmp_path / "nested" / "model-profile.schema.json"
    runtime_path = str(Path(__file__).resolve().parents[2] / "runtime")
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            filter(None, (runtime_path, os.environ.get("PYTHONPATH")))
        ),
    }

    subprocess.run(
        [
            sys.executable,
            "-m",
            "eidos_runtime.contracts.export_model_profile",
            "--output",
            str(output),
        ],
        check=True,
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
    )

    assert json.loads(output.read_text(encoding="utf-8"))["title"] == (
        "ModelProfileContractBundle"
    )


def test_shared_model_profile_fixture_is_valid_runtime_wire_json() -> None:
    profile = ModelProfile.model_validate_json(FIXTURE_PATH.read_bytes())

    assert profile.auth_reference == "local:test-api-key"
    assert profile.to_wire_dict()["supportsTools"] is True
