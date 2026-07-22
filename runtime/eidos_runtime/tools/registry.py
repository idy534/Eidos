from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import threading
from typing import Literal, Protocol

from pydantic import Field, StrictInt, StrictStr, field_validator

from eidos_runtime.protocol.schemas import ClosedModel, StepToolSnapshotDto
from eidos_runtime.model.client import ModelToolDefinition


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_ALLOWED_SCHEMA_KEYS = {
    "type", "properties", "required", "additionalProperties", "items",
    "enum", "const", "default", "minimum", "maximum", "minLength",
    "maxLength", "minItems", "maxItems", "description",
}
MAX_ACTIVATED_TOOLS = 16
MAX_ACTIVATED_SCHEMA_BYTES = 128 * 1024
MAX_SINGLE_SCHEMA_BYTES = 32 * 1024


class ToolSpec(ClosedModel):
    name: StrictStr
    description: StrictStr
    side_effect: Literal["none", "workspace", "eidos_state", "shell", "external"] = Field(
        alias="sideEffect"
    )
    approval_required: bool = Field(alias="approvalRequired")
    timeout_seconds: StrictInt = Field(alias="timeoutSeconds", ge=1, le=600)
    batch_policy: Literal["parallel", "single"] = Field(
        default="single", alias="batchPolicy"
    )
    visibility: Literal["direct", "deferred"] = "direct"
    input_schema: dict[str, object] = Field(alias="inputSchema")
    result_schema: dict[str, object] = Field(alias="resultSchema")

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not _TOOL_NAME.fullmatch(value):
            raise ValueError("invalid_tool_name")
        return value


class ToolProvenance(ClosedModel):
    kind: Literal["builtin", "skill", "mcp"]
    source_id: StrictStr = Field(alias="sourceId", min_length=1, max_length=128)
    source_version: StrictStr = Field(alias="sourceVersion", min_length=1, max_length=64)
    content_hash: StrictStr = Field(alias="contentHash")
    plugin_id: StrictStr | None = Field(default=None, alias="pluginId")
    server_id: StrictStr | None = Field(default=None, alias="serverId")
    skill_id: StrictStr | None = Field(default=None, alias="skillId")

    @field_validator("content_hash")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        if not _SHA256.fullmatch(value):
            raise ValueError("invalid_content_hash")
        return value


class ToolAdapter(Protocol):
    execution_kind: Literal[
        "read", "file", "shell", "external", "eidos_state", "network_eidos_state"
    ]

    def effective_arguments(self, arguments: object) -> dict[str, object] | None: ...

    def execute(
        self, arguments: dict[str, object], cancel: threading.Event
    ) -> dict[str, object]: ...


@dataclass(frozen=True)
class ToolRegistryEntry:
    spec: ToolSpec
    provenance: ToolProvenance
    adapter: ToolAdapter


@dataclass(frozen=True)
class QuarantinedTool:
    name: str
    code: str


@dataclass(frozen=True)
class StepToolSnapshot:
    available_names: tuple[str, ...]
    direct_names: tuple[str, ...]
    deferred_names: tuple[str, ...]
    activated_names: tuple[str, ...]
    spec_hashes: tuple[tuple[str, str], ...]
    definitions_hash: str
    tool_set_hash: str

    def as_dict(self) -> dict[str, object]:
        return StepToolSnapshotDto.model_validate({
            "schemaVersion": 1,
            "availableNames": list(self.available_names),
            "directNames": list(self.direct_names),
            "deferredNames": list(self.deferred_names),
            "activatedNames": list(self.activated_names),
            "specHashes": {name: value for name, value in self.spec_hashes},
            "definitionsHash": self.definitions_hash,
            "toolSetHash": self.tool_set_hash,
        }).to_json_value()


class ToolRegistry:
    def __init__(
        self,
        entries: tuple[ToolRegistryEntry, ...],
        *,
        quarantined: tuple[QuarantinedTool, ...] = (),
    ) -> None:
        ordered = sorted(entries, key=lambda entry: entry.spec.name.encode("utf-8"))
        names: set[str] = set()
        for entry in ordered:
            if entry.spec.name in names:
                raise ValueError("duplicate_tool_name")
            _validate_entry(entry)
            names.add(entry.spec.name)
        self._entries = tuple(ordered)
        self._by_name = {entry.spec.name: entry for entry in ordered}
        self.quarantined = quarantined

    @classmethod
    def build(
        cls,
        *,
        builtin_entries: tuple[ToolRegistryEntry, ...],
        external_entries: tuple[ToolRegistryEntry, ...],
    ) -> ToolRegistry:
        builtins = cls(builtin_entries)
        accepted = list(builtins.entries)
        names = set(builtins.names)
        quarantined: list[QuarantinedTool] = []
        for entry in sorted(
            external_entries, key=lambda value: value.spec.name.encode("utf-8")
        ):
            try:
                if entry.spec.name in names:
                    raise ValueError("duplicate_tool_name")
                _validate_entry(entry)
            except ValueError as error:
                quarantined.append(QuarantinedTool(entry.spec.name, str(error)))
                continue
            accepted.append(entry)
            names.add(entry.spec.name)
        return cls(tuple(accepted), quarantined=tuple(quarantined))

    @property
    def entries(self) -> tuple[ToolRegistryEntry, ...]:
        return self._entries

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self._by_name)

    def get(self, name: str) -> ToolRegistryEntry | None:
        return self._by_name.get(name)

    def model_definitions(
        self, activated_names: tuple[str, ...] = ()
    ) -> tuple[ModelToolDefinition, ...]:
        active = set(self._bounded_activated(activated_names))
        return tuple(ModelToolDefinition(
            name=entry.spec.name,
            description=entry.spec.description,
            parameters_json_schema=entry.spec.input_schema,
        ) for entry in self._entries if (
            entry.spec.visibility == "direct" or entry.spec.name in active
        ))

    def snapshot(
        self, *, activated_names: tuple[str, ...] = ()
    ) -> StepToolSnapshot:
        deferred = tuple(
            entry.spec.name for entry in self._entries
            if entry.spec.visibility == "deferred"
        )
        activated = self._bounded_activated(activated_names)
        direct = tuple(
            entry.spec.name for entry in self._entries
            if entry.spec.visibility == "direct"
        )
        available = tuple(sorted(
            (*direct, *activated), key=lambda value: value.encode("utf-8")
        ))
        spec_hashes = tuple((name, _hash_json(
            self._by_name[name].spec.model_dump(mode="json", by_alias=True)
        )) for name in available)
        definitions = self.model_definitions(activated)
        definitions_hash = _hash_json([
            definition.model_dump(mode="json") for definition in definitions
        ])
        tool_set_hash = _hash_json({
            "availableNames": available,
            "directNames": direct,
            "deferredNames": deferred,
            "activatedNames": activated,
            "specHashes": spec_hashes,
            "definitionsHash": definitions_hash,
        })
        return StepToolSnapshot(
            available, direct, deferred, activated, spec_hashes,
            definitions_hash, tool_set_hash,
        )

    def _bounded_activated(self, activated_names: tuple[str, ...]) -> tuple[str, ...]:
        deferred = {
            entry.spec.name for entry in self._entries
            if entry.spec.visibility == "deferred"
        }
        accepted: list[str] = []
        total = 0
        for name in sorted(
            set(activated_names) & deferred, key=lambda value: value.encode("utf-8")
        ):
            entry = self._by_name[name]
            size = len(json.dumps(
                entry.spec.input_schema,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8"))
            if size > MAX_SINGLE_SCHEMA_BYTES or total + size > MAX_ACTIVATED_SCHEMA_BYTES:
                continue
            accepted.append(name)
            total += size
            if len(accepted) >= MAX_ACTIVATED_TOOLS:
                break
        return tuple(accepted)


def _validate_entry(entry: ToolRegistryEntry) -> None:
    if not hasattr(entry.adapter, "effective_arguments") or not hasattr(
        entry.adapter, "execute"
    ):
        raise ValueError("missing_tool_adapter")
    if not _valid_schema(entry.spec.input_schema) or not _valid_schema(
        entry.spec.result_schema
    ):
        raise ValueError("invalid_tool_schema")
    if entry.provenance.kind == "mcp":
        if (
            not entry.provenance.plugin_id
            or not entry.provenance.server_id
            or not entry.spec.name.startswith(
                f"mcp__{entry.provenance.server_id}__"
            )
        ):
            raise ValueError("invalid_mcp_provenance")
    if entry.provenance.kind == "skill" and (
        not entry.provenance.plugin_id or not entry.provenance.skill_id
    ):
        raise ValueError("invalid_skill_provenance")


def _hash_json(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _valid_schema(schema: object, *, nested: bool = False) -> bool:
    if not isinstance(schema, dict) or not set(schema) <= _ALLOWED_SCHEMA_KEYS:
        return False
    schema_type = schema.get("type")
    if schema_type not in {"object", "array", "string", "integer", "number", "boolean"}:
        return False
    if schema_type == "object":
        properties = schema.get("properties")
        if not isinstance(properties, dict) or schema.get("additionalProperties") is not False:
            return False
        if not all(isinstance(key, str) and _valid_schema(value, nested=True) for key, value in properties.items()):
            return False
        required = schema.get("required", [])
        if not isinstance(required, list) or not all(
            isinstance(key, str) and key in properties for key in required
        ):
            return False
    elif schema_type == "array" and not _valid_schema(schema.get("items"), nested=True):
        return False
    elif "properties" in schema or "required" in schema or "additionalProperties" in schema:
        return False
    return nested or schema_type == "object"
