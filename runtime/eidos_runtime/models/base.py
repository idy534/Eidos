from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from pydantic.json_schema import (
    DEFAULT_REF_TEMPLATE,
    GenerateJsonSchema,
    JsonSchemaMode,
)


class EidosModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
    )

    def to_internal_dict(self, *, exclude_none: bool = False) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            by_alias=False,
            exclude_none=exclude_none,
        )

    def to_wire_dict(self, *, exclude_none: bool = False) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=exclude_none,
        )

    @classmethod
    def model_json_schema(
        cls,
        *,
        by_alias: bool = False,
        ref_template: str = DEFAULT_REF_TEMPLATE,
        schema_generator: type[GenerateJsonSchema] = GenerateJsonSchema,
        mode: JsonSchemaMode = "validation",
        union_format: Literal["any_of", "primitive_type_array"] = "any_of",
    ) -> dict[str, object]:
        return super().model_json_schema(
            by_alias=by_alias,
            ref_template=ref_template,
            schema_generator=schema_generator,
            mode=mode,
            union_format=union_format,
        )


class EidosStrictModel(EidosModel):
    model_config = ConfigDict(strict=True)


class EidosFrozenModel(EidosModel):
    model_config = ConfigDict(frozen=True)


class EidosFrozenStrictModel(EidosStrictModel):
    model_config = ConfigDict(frozen=True)
