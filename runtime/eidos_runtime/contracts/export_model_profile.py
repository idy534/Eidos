from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from pydantic.json_schema import GenerateJsonSchema, JsonSchemaMode

from eidos_runtime.model_gateway.models import ModelProfile
from eidos_runtime.models import EidosFrozenStrictModel


GENERATED_COMMENT = (
    "Generated from Eidos Runtime Pydantic models. Do not edit manually."
)
DEFAULT_OUTPUT = (
    Path(__file__).resolve().parents[3]
    / "contracts"
    / "generated"
    / "model-profile.schema.json"
)


class ModelProfileContractBundle(EidosFrozenStrictModel):
    """Schema-only root that retains the Desktop Model Profile contract."""

    profile: ModelProfile


class Draft7ModelProfileSchema(GenerateJsonSchema):
    """Adapt Pydantic's schema to the draft accepted by json-schema-to-typescript."""

    schema_dialect = "http://json-schema.org/draft-07/schema#"

    def field_title_should_be_set(self, schema: object) -> bool:
        # Property titles are presentation metadata. Omitting their generated
        # values keeps json-schema-to-typescript from creating aliases such as
        # `SupportsTools` for otherwise inline primitive fields.
        return False

    def generate(
        self,
        schema: object,
        mode: JsonSchemaMode = "validation",
    ) -> dict[str, object]:
        generated = super().generate(schema, mode=mode)
        definitions = generated.pop("$defs", None)
        if definitions is not None:
            generated["definitions"] = definitions
        generated["$schema"] = self.schema_dialect
        return generated


def model_profile_schema() -> dict[str, object]:
    schema = ModelProfileContractBundle.model_json_schema(
        by_alias=True,
        ref_template="#/definitions/{model}",
        schema_generator=Draft7ModelProfileSchema,
    )
    schema["$comment"] = GENERATED_COMMENT
    return schema


def export_schema(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(
        model_profile_schema(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    output.write_text(f"{serialized}\n", encoding="utf-8", newline="\n")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export the Model Profile Pydantic contract as JSON Schema."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    export_schema(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
